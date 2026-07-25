from __future__ import annotations

import asyncio
import signal
import time
from typing import Any

from .config import EdgeConfig
from .gateway import GatewayClient
from .packages import PackageManager
from .secrets import SecretVault
from .storage import EdgeStore
from .supervisor import ExtensionSupervisor


class EdgeService:
    def __init__(self, config: EdgeConfig) -> None:
        self.config = config
        config.state_directory.mkdir(parents=True, exist_ok=True)
        self.store = EdgeStore(config.state_directory / "edge.sqlite3")
        self.vault = SecretVault(config.state_directory / "secrets")
        self.packages = PackageManager(config.state_directory / "packages", self.store, developer_mode=config.developer_mode)
        self.supervisor = ExtensionSupervisor(
            self.store,
            self.vault,
            config.state_directory / "connections",
            self.handle_extension_event,
        )
        self.gateway = GatewayClient(config, self.store, self.vault, self.handle_gateway_message)
        self._health_task: asyncio.Task[None] | None = None
        self._job_tasks: set[asyncio.Task[None]] = set()

    def connection_snapshot(self, request_id: str | None = None) -> dict[str, Any]:
        return {
            "type": "connection.snapshot",
            "requestId": request_id,
            "connections": [
                {
                    "connectionId": item.id,
                    "extensionId": item.extension_id,
                    "extensionVersion": item.extension_version,
                    "packageDigest": item.package_digest,
                    "displayName": item.display_name,
                    "kind": item.kind,
                    "configurationState": "ready",
                    "instanceId": self.supervisor.processes.get(item.id).instance_id if item.id in self.supervisor.processes else None,
                    "enabled": item.enabled,
                    "health": {
                        "state": item.health_state,
                        "message": item.health_message,
                        "sessionExpiresAt": item.session_expires_at,
                        "lastSuccessAt": item.last_success_at,
                    },
                }
                for item in self.store.connections()
            ],
        }

    async def publish_connection_snapshot(self) -> None:
        await self.gateway.send(self.connection_snapshot())

    async def handle_extension_event(self, process: Any, message: dict[str, Any]) -> None:
        connection = process.connection
        event_id = self.store.enqueue_event(connection.id, {
            "connectionId": connection.id,
            "instanceId": process.instance_id,
            "extensionId": connection.extension_id,
            "extensionVersion": connection.extension_version,
            "packageDigest": connection.package_digest,
            "eventType": message.get("eventType"),
            "eventVersion": message.get("eventVersion"),
            "eventId": message.get("eventId"),
            "payload": message.get("payload") or {},
            "scope": message.get("scope") or {},
        })
        await self.gateway.send({"type": "event", "event": {
            **message,
            "connectionId": connection.id,
            "instanceId": process.instance_id,
            "extensionId": connection.extension_id,
            "extensionVersion": connection.extension_version,
            "packageDigest": connection.package_digest,
            "eventId": event_id,
        }})

    async def resend_events(self) -> None:
        for _event_id, _connection_id, payload in self.store.pending_events():
            await self.gateway.send({"type": "event", "event": payload})

    async def start_instances(self) -> None:
        for connection in self.store.connections():
            if not connection.enabled:
                continue
            try:
                await self.supervisor.start(connection)
                await self.supervisor.health(connection.id)
            except Exception as error:
                self.store.update_health(connection.id, {"state": "degraded", "message": str(error)[:500]})

    async def handle_gateway_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        kind = message.get("type")
        if kind in (
            "heartbeat.accepted",
            "connection.snapshot.accepted",
            "job.lease.extended",
            "job.result.accepted",
            "runtime.specifications.accepted",
        ):
            return None
        if kind == "runtime.specifications":
            connection_id = str(message["connectionId"])
            process = self.supervisor.processes.get(connection_id)
            if not process:
                return {
                    "type": "runtime.specifications.accepted",
                    "requestId": message.get("requestId"),
                    "connectionId": connection_id,
                    "accepted": False,
                }
            response = await self.supervisor.request(process, {
                "type": "specifications",
                "captureSpecification": message.get("captureSpecification") or {},
                "expectedEvents": message.get("expectedEvents") or {},
            })
            return {
                "type": "runtime.specifications.accepted",
                "requestId": message.get("requestId"),
                "connectionId": connection_id,
                "accepted": response.get("type") == "specifications.applied",
            }
        if kind == "gateway.connected":
            await self.resend_events()
            return None
        if kind == "event.accepted" and message.get("eventId"):
            self.store.acknowledge_event(str(message["eventId"]))
            return None
        if kind == "connection.sync":
            return self.connection_snapshot(message.get("requestId"))
        if kind in (
            "action.request",
            "adapter.operation.request",
            "binding-catalog.request",
            "input-resolver.request",
        ):
            task = asyncio.create_task(self._execute_job(message))
            self._job_tasks.add(task)
            task.add_done_callback(self._job_tasks.discard)
            return None
        if kind == "connection.enable":
            connection_id = str(message["connectionId"])
            enabled = bool(message["enabled"])
            self.store.set_enabled(connection_id, enabled)
            if enabled:
                connection = next(item for item in self.store.connections() if item.id == connection_id)
                if connection_id not in self.supervisor.processes:
                    await self.supervisor.start(connection)
            else:
                await self.supervisor.stop(connection_id)
            return {"type": "connection.enable.accepted", "requestId": message.get("requestId"), "connectionId": connection_id, "enabled": enabled}
        return {"type": "error", "requestId": message.get("requestId"), "code": "MESSAGE_UNSUPPORTED"}

    async def _execute_job(self, message: dict[str, Any]) -> None:
        job = dict(message["job"])
        connection_id = str(message["connectionId"])
        job_kind = job.get("jobKind")
        if job_kind in ("binding-catalog", "input-resolver"):
            job["type"] = job_kind
        else:
            job["type"] = "adapter.operation" if str(message["type"]).startswith("adapter.") else "action"
            job["contractId"] = str(job.get("nodeType") or job.get("contractId") or "")
            job["contractVersion"] = str(job.get("nodeVersion") or job.get("contractVersion") or "1.0.0")

        async def extend_lease() -> None:
            while True:
                await asyncio.sleep(45)
                await self.gateway.send({
                    "type": "job.lease.extend",
                    "jobId": job["jobId"],
                    "leaseToken": job["leaseToken"],
                    "connectionId": connection_id,
                })

        extender = asyncio.create_task(extend_lease())
        try:
            result = await self.supervisor.execute(connection_id, job)
            process = self.supervisor.processes.get(connection_id)
            await self.gateway.send({
                "type": "job.result",
                "jobId": job["jobId"],
                "leaseToken": job["leaseToken"],
                "connectionId": connection_id,
                "instanceId": process.instance_id if process else message.get("instanceId"),
                "result": result,
            })
        finally:
            extender.cancel()

    async def health_loop(self) -> None:
        next_health_check = 0.0
        while True:
            changed = False
            check_health = time.monotonic() >= next_health_check
            connections = {item.id: item for item in self.store.connections()}
            for connection_id in list(self.supervisor.processes):
                current = connections.get(connection_id)
                if not current or not current.enabled:
                    await self.supervisor.stop(connection_id)
                    changed = True
            for connection_id, current in connections.items():
                if not current.enabled:
                    continue
                try:
                    if connection_id not in self.supervisor.processes:
                        await self.supervisor.start(current)
                        await self.supervisor.health(connection_id)
                        changed = True
                    running = self.supervisor.processes[connection_id].connection
                    if current.extension_version != running.extension_version or current.package_digest != running.package_digest:
                        # stop() takes the process request lock, so in-flight jobs drain
                        # before the migration snapshot is taken.
                        await self.supervisor.stop(connection_id)
                        snapshot = self.supervisor.snapshot_state(connection_id)
                        try:
                            await self.supervisor.start(
                                current,
                                previous_version=running.extension_version,
                            )
                            await self.supervisor.health(connection_id)
                        except Exception as update_error:
                            await self.supervisor.stop(connection_id)
                            self.supervisor.restore_state(connection_id, snapshot)
                            self.store.rollback_connection(connection_id)
                            previous = next(item for item in self.store.connections() if item.id == connection_id)
                            await self.supervisor.start(previous)
                            await self.supervisor.health(connection_id)
                            self.store.update_health(connection_id, {
                                "state": "degraded",
                                "message": f"Update rolled back: {str(update_error)[:400]}",
                            })
                        finally:
                            self.supervisor.discard_snapshot(snapshot)
                        changed = True
                    elif check_health:
                        before = next(
                            item for item in self.store.connections()
                            if item.id == connection_id
                        )
                        await self.supervisor.health(connection_id)
                        after = next(
                            item for item in self.store.connections()
                            if item.id == connection_id
                        )
                        changed = changed or (
                            before.health_state != after.health_state
                            or before.health_message != after.health_message
                        )
                except Exception as error:
                    self.store.update_health(connection_id, {"state": "degraded", "message": str(error)[:500]})
                    changed = True
            if check_health:
                next_health_check = time.monotonic() + 30
            if changed:
                await self.publish_connection_snapshot()
            await asyncio.sleep(1)

    async def run(self) -> None:
        await self.start_instances()
        self._health_task = asyncio.create_task(self.health_loop())
        try:
            await self.gateway.run()
        finally:
            if self._health_task:
                self._health_task.cancel()
            for task in self._job_tasks:
                task.cancel()
            await self.supervisor.stop_all()

    async def shutdown(self) -> None:
        self.gateway.stop()
        await self.supervisor.stop_all()


async def serve(config: EdgeConfig | None = None) -> None:
    service = EdgeService(config or EdgeConfig.load())
    loop = asyncio.get_running_loop()
    for name in ("SIGTERM", "SIGINT"):
        if hasattr(signal, name):
            try:
                loop.add_signal_handler(getattr(signal, name), service.gateway.stop)
            except NotImplementedError:
                pass
    await service.run()
