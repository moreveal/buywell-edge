from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from .contracts import ActionContext, AdapterContext, ExecutionOutcome, ExtensionDefinition, Health, HealthState


@dataclass
class RuntimeSession:
    connection_id: str
    instance_id: str
    config: dict[str, Any]
    secrets: dict[str, str]
    state_directory: Path
    emit: Callable[[dict[str, Any]], Awaitable[None]]
    capture_specification: dict[str, Any]
    expected_events: dict[str, Any]

    async def emit_event(
        self,
        event_type: str,
        event_version: str,
        payload: dict[str, Any],
        scope: dict[str, Any],
        *,
        event_id: str | None = None,
    ) -> None:
        import uuid
        await self.emit({
            "type": "event",
            "eventId": event_id or str(uuid.uuid4()),
            "eventType": event_type,
            "eventVersion": event_version,
            "payload": payload,
            "scope": scope,
        })


class ExtensionRuntime:
    def __init__(self, extension: ExtensionDefinition, emit: Callable[[dict[str, Any]], Awaitable[None]] | None = None) -> None:
        self.extension = extension
        self.session: RuntimeSession | None = None
        self.emit = emit or _discard

    async def handle(self, message: dict[str, Any]) -> dict[str, Any]:
        kind = message.get("type")
        request_id = str(message.get("requestId", ""))
        if kind == "initialize":
            self.session = RuntimeSession(
                connection_id=str(message["connectionId"]),
                instance_id=str(message["instanceId"]),
                config=dict(message.get("config") or {}),
                secrets={key: str(value) for key, value in (message.get("secrets") or {}).items()},
                state_directory=Path(message["stateDirectory"]),
                emit=self.emit,
                capture_specification={},
                expected_events={},
            )
            self.session.state_directory.mkdir(parents=True, exist_ok=True)
            previous_version = message.get("previousVersion")
            if previous_version and self.extension.migration_handler:
                result = self.extension.migration_handler(
                    self.session,
                    str(previous_version),
                    self.extension.version,
                )
                if asyncio.iscoroutine(result):
                    await result
            if self.extension.start_handler:
                result = self.extension.start_handler(self.session)
                if asyncio.iscoroutine(result):
                    await result
            return {"type": "ready", "requestId": request_id, "manifest": self.extension.manifest()}
        if not self.session:
            raise RuntimeError("Extension is not initialized")
        if kind == "specifications":
            self.session.capture_specification = dict(message.get("captureSpecification") or {})
            self.session.expected_events = dict(message.get("expectedEvents") or {})
            return {"type": "specifications.applied", "requestId": request_id}
        if kind == "health":
            health = Health(state=HealthState.HEALTHY)
            if self.extension.health_handler:
                value = self.extension.health_handler(self.session)
                if asyncio.iscoroutine(value):
                    value = await value
                health = value if isinstance(value, Health) else Health.model_validate(value)
            return {"type": "health.result", "requestId": request_id, "health": health.model_dump(mode="json", exclude_none=True)}
        if kind in ("action", "adapter.operation"):
            prefix = "action" if kind == "action" else "operation"
            contract_id = str(message["contractId"])
            contract_version = str(message["contractVersion"])
            handler = self.extension.handlers.get(f"{prefix}:{contract_id}@{contract_version}")
            if not handler:
                return {"type": "job.result", "requestId": request_id, "status": "error", "error": {"code": "CONTRACT_NOT_FOUND", "message": "Extension contract is unavailable", "retryable": False}}
            common = {
                "connection_id": self.session.connection_id,
                "instance_id": self.session.instance_id,
                "job_id": str(message["jobId"]),
                "idempotency_key": str(message["idempotencyKey"]),
                "state": self.session.state_directory,
                "config": self.session.config,
                "secrets": self.session.secrets,
                "event_payload": dict((message.get("context") or {}).get("eventPayload") or {}),
                "event_scope": dict((message.get("context") or {}).get("eventScope") or {}),
            }
            context = AdapterContext(operation_id=contract_id, **common) if kind == "adapter.operation" else ActionContext(**common)
            try:
                outputs = await handler(context, dict(message.get("inputs") or {}))
                return {"type": "job.result", "requestId": request_id, "status": "success", "outputs": outputs}
            except Exception as error:
                return {"type": "job.result", "requestId": request_id, "status": "error", "error": {"code": getattr(error, "code", "EXTENSION_ERROR"), "message": str(error)[:2_000], "retryable": bool(getattr(error, "retryable", False))}}
        if kind in ("binding-catalog", "input-resolver"):
            contract_id = str(
                message["catalogId"] if kind == "binding-catalog" else message["resolverId"]
            )
            contract_version = str(
                message["catalogVersion"] if kind == "binding-catalog" else message["resolverVersion"]
            )
            handler = self.extension.protocol_handlers.get(
                f"{kind}:{contract_id}@{contract_version}"
            )
            if not handler:
                return {
                    "type": "job.result",
                    "requestId": request_id,
                    "status": "error",
                    "error": {
                        "code": "CONTRACT_NOT_FOUND",
                        "message": "Extension protocol contract is unavailable",
                        "retryable": False,
                    },
                }
            context = ActionContext(
                connection_id=self.session.connection_id,
                instance_id=self.session.instance_id,
                job_id=str(message["jobId"]),
                idempotency_key=str(message.get("idempotencyKey") or message["jobId"]),
                state=self.session.state_directory,
                config=self.session.config,
                secrets=self.session.secrets,
                event_payload=dict(message.get("eventPayload") or {}),
                event_scope=dict(message.get("eventScope") or {}),
            )
            try:
                value = handler(context, message)
                if asyncio.iscoroutine(value):
                    value = await value
                return {"type": "job.result", "requestId": request_id, "status": "success", "value": value}
            except Exception as error:
                return {
                    "type": "job.result",
                    "requestId": request_id,
                    "status": "error",
                    "error": {
                        "code": getattr(error, "code", "EXTENSION_ERROR"),
                        "message": str(error)[:2_000],
                        "retryable": bool(getattr(error, "retryable", False)),
                    },
                }
        if kind == "execution-outcome":
            if not self.extension.execution_outcome_handler:
                return {"type": "job.result", "requestId": request_id, "status": "error", "error": {"code": "CONTRACT_NOT_FOUND", "message": "Execution outcome handler is unavailable", "retryable": False}}
            context = ActionContext(
                connection_id=self.session.connection_id,
                instance_id=self.session.instance_id,
                job_id=str(message["jobId"]),
                idempotency_key=str(message["idempotencyKey"]),
                state=self.session.state_directory,
                config=self.session.config,
                secrets=self.session.secrets,
                event_payload={},
                event_scope=dict((message.get("context") or {}).get("eventScope") or {}),
            )
            try:
                outcome = ExecutionOutcome.model_validate(message)
                value = self.extension.execution_outcome_handler(context, outcome)
                if asyncio.iscoroutine(value):
                    value = await value
                outputs = value if isinstance(value, dict) else {}
                return {"type": "job.result", "requestId": request_id, "status": "success", "outputs": outputs}
            except Exception as error:
                return {"type": "job.result", "requestId": request_id, "status": "error", "error": {"code": getattr(error, "code", "EXTENSION_ERROR"), "message": str(error)[:2_000], "retryable": bool(getattr(error, "retryable", False))}}
        if kind == "shutdown":
            if self.extension.stop_handler:
                value = self.extension.stop_handler(self.session)
                if asyncio.iscoroutine(value):
                    await value
            return {"type": "stopped", "requestId": request_id}
        raise ValueError(f"Unsupported runtime message: {kind}")


async def serve(extension: ExtensionDefinition) -> None:
    output_lock = asyncio.Lock()

    async def write(value: dict[str, Any]) -> None:
        async with output_lock:
            sys.stdout.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()

    runtime = ExtensionRuntime(extension, write)
    loop = asyncio.get_running_loop()
    while line := await loop.run_in_executor(None, sys.stdin.readline):
        try:
            message = json.loads(line)
            response = await runtime.handle(message)
        except Exception as error:
            response = {"type": "error", "requestId": "", "error": {"code": "RUNTIME_PROTOCOL_ERROR", "message": str(error)[:1_000]}}
        await write(response)
        if response.get("type") == "stopped":
            return


async def _discard(_value: dict[str, Any]) -> None:
    return None


def load(reference: str) -> ExtensionDefinition:
    module_name, _, object_name = reference.partition(":")
    value = getattr(importlib.import_module(module_name), object_name)
    if not isinstance(value, ExtensionDefinition):
        raise TypeError("Runtime entrypoint must be an ExtensionDefinition")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("entrypoint")
    args = parser.parse_args()
    asyncio.run(serve(load(args.entrypoint)))


if __name__ == "__main__":
    main()
