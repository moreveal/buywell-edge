from __future__ import annotations

import asyncio
import json
import sys
import uuid
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from .secrets import SecretVault
from .storage import ConnectionRecord, EdgeStore


@dataclass
class ExtensionProcess:
    instance_id: str
    connection: ConnectionRecord
    process: asyncio.subprocess.Process
    lock: asyncio.Lock
    request_sequence: int = 0
    pending: dict[str, asyncio.Future[dict[str, Any]]] | None = None
    reader_task: asyncio.Task[None] | None = None
    stderr_task: asyncio.Task[None] | None = None


EventHandler = Callable[[ExtensionProcess, dict[str, Any]], Awaitable[None]]


class ExtensionSupervisor:
    def __init__(
        self,
        store: EdgeStore,
        vault: SecretVault,
        state_root: Path,
        event_handler: EventHandler | None = None,
    ) -> None:
        self.store = store
        self.vault = vault
        self.state_root = state_root
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.logs_root = state_root.parent / "logs"
        self.logs_root.mkdir(parents=True, exist_ok=True)
        self.processes: dict[str, ExtensionProcess] = {}
        self.event_handler = event_handler

    async def start(
        self,
        connection: ConnectionRecord,
        *,
        previous_version: str | None = None,
    ) -> ExtensionProcess:
        installed = self.store.package(connection.extension_id, connection.extension_version, connection.package_digest)
        if not installed:
            raise RuntimeError("The exact extension package is not installed")
        manifest, directory = installed
        entrypoint = manifest["runtime"]["entrypoint"]
        source = directory / "extension"
        dependency_directory = directory / "dependencies"
        environment = os.environ.copy()
        python_paths = [str(source)]
        if dependency_directory.exists():
            python_paths.append(str(dependency_directory))
        if environment.get("PYTHONPATH"):
            python_paths.append(environment["PYTHONPATH"])
        environment["PYTHONPATH"] = os.pathsep.join(python_paths)
        instance_id = str(uuid.uuid4())
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "buywell_edge_sdk.runtime",
            entrypoint,
            cwd=source,
            env=environment,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        item = ExtensionProcess(instance_id, connection, process, asyncio.Lock(), pending={})
        self.processes[connection.id] = item
        item.reader_task = asyncio.create_task(self._read_stdout(item))
        item.stderr_task = asyncio.create_task(self._read_stderr(item))
        response = await self.request(item, {
            "type": "initialize",
            "connectionId": connection.id,
            "instanceId": instance_id,
            "config": connection.config,
            "secrets": self.vault.get(connection.secret_ref),
            "stateDirectory": str(self.state_root / connection.id),
            "previousVersion": previous_version,
        })
        if response.get("type") != "ready":
            await self.stop(connection.id)
            raise RuntimeError("Extension failed its readiness handshake")
        return item

    async def request(self, item: ExtensionProcess, payload: dict[str, Any], timeout: float = 60) -> dict[str, Any]:
        if not item.process.stdin:
            raise RuntimeError("Extension stdio is unavailable")
        async with item.lock:
            item.request_sequence += 1
            request_id = f"{item.instance_id}:{item.request_sequence}"
            wire = {**payload, "requestId": request_id}
            future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
            assert item.pending is not None
            item.pending[request_id] = future
            item.process.stdin.write((json.dumps(wire, ensure_ascii=False, separators=(",", ":")) + "\n").encode())
            await item.process.stdin.drain()
            try:
                return await asyncio.wait_for(future, timeout)
            finally:
                item.pending.pop(request_id, None)

    async def _read_stdout(self, item: ExtensionProcess) -> None:
        assert item.process.stdout is not None
        while line := await item.process.stdout.readline():
            try:
                message = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if message.get("type") == "event" and self.event_handler:
                await self.event_handler(item, message)
                continue
            request_id = str(message.get("requestId", ""))
            future = (item.pending or {}).get(request_id)
            if future and not future.done():
                future.set_result(message)
        for future in (item.pending or {}).values():
            if not future.done():
                future.set_exception(RuntimeError("Extension process stopped"))

    async def _read_stderr(self, item: ExtensionProcess) -> None:
        assert item.process.stderr is not None
        while line := await item.process.stderr.readline():
            text = line[:16_384].decode("utf-8", errors="replace")
            for value in self.vault.get(item.connection.secret_ref).values():
                if len(value) >= 4:
                    text = text.replace(value, "[REDACTED]")
            text = re.sub(
                r"(?i)(authorization|token|password|secret|cookie)([\"'\s:=]+)([^,\s\"']+)",
                r"\1\2[REDACTED]",
                text,
            )
            await asyncio.to_thread(
                self._append_log,
                item.connection.id,
                text,
            )

    def _append_log(self, connection_id: str, text: str) -> None:
        path = self.logs_root / f"{connection_id}.log"
        if path.exists() and path.stat().st_size > 2 * 1024 * 1024:
            rotated = path.with_suffix(".log.1")
            if rotated.exists():
                rotated.unlink()
            os.replace(path, rotated)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(text)

    def recent_logs(self, limit: int = 200) -> str:
        lines: list[str] = []
        for path in sorted(self.logs_root.glob("*.log")):
            try:
                values = path.read_text("utf-8", errors="replace").splitlines()
            except OSError:
                continue
            lines.extend(f"{path.stem[:8]}  {value}" for value in values[-limit:])
        return "\n".join(lines[-limit:])

    async def health(self, connection_id: str) -> dict[str, Any]:
        item = self.processes[connection_id]
        response = await self.request(item, {"type": "health"}, timeout=15)
        health = dict(response.get("health") or {"state": "degraded", "message": "Health response is unavailable"})
        self.store.update_health(connection_id, health)
        return health

    async def execute(self, connection_id: str, job: dict[str, Any]) -> dict[str, Any]:
        item = self.processes[connection_id]
        key = str(job["idempotencyKey"])
        existing = self.store.idempotency_result(key)
        if existing and existing["status"] in ("success", "error"):
            return dict(existing["value"])
        if not self.store.begin_idempotent(key) and existing:
            raise RuntimeError("An identical operation is already running")
        response = await self.request(item, job, timeout=900)
        status = "success" if response.get("status") == "success" else "error"
        self.store.finish_idempotent(key, status, response)
        return response

    async def stop(self, connection_id: str) -> None:
        item = self.processes.pop(connection_id, None)
        if not item:
            return
        try:
            await self.request(item, {"type": "shutdown"}, timeout=10)
        except Exception:
            pass
        if item.process.returncode is None:
            item.process.terminate()
            try:
                await asyncio.wait_for(item.process.wait(), 10)
            except TimeoutError:
                item.process.kill()
                await item.process.wait()
        for task in (item.reader_task, item.stderr_task):
            if task:
                task.cancel()

    async def stop_all(self) -> None:
        await asyncio.gather(*(self.stop(key) for key in list(self.processes)), return_exceptions=True)

    def snapshot_state(self, connection_id: str) -> Path:
        source = self.state_root / connection_id
        snapshot = self.state_root / ".snapshots" / connection_id / str(uuid.uuid4())
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        if source.exists():
            shutil.copytree(source, snapshot)
        else:
            snapshot.mkdir()
        return snapshot

    def restore_state(self, connection_id: str, snapshot: Path) -> None:
        target = self.state_root / connection_id
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(snapshot, target)

    def discard_snapshot(self, snapshot: Path) -> None:
        snapshots_root = (self.state_root / ".snapshots").resolve()
        resolved = snapshot.resolve()
        if snapshots_root not in resolved.parents:
            raise ValueError("Snapshot is outside the Edge snapshot directory")
        shutil.rmtree(resolved, ignore_errors=True)
