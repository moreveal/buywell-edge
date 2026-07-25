from __future__ import annotations

import asyncio
import json
import platform
import random
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from websockets.asyncio.client import connect

from buywell_edge_sdk.contracts import EDGE_PROTOCOL_VERSION

from . import __version__
from .config import EdgeConfig
from .secrets import SecretVault
from .storage import EdgeStore

MessageHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None]]


class GatewayClient:
    def __init__(self, config: EdgeConfig, store: EdgeStore, vault: SecretVault, handler: MessageHandler) -> None:
        self.config = config
        self.store = store
        self.vault = vault
        self.handler = handler
        self._stop = asyncio.Event()
        self._outbound: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)

    async def send(self, payload: dict[str, Any]) -> None:
        await self._outbound.put(payload)

    async def pair(self, code: str, name: str) -> tuple[str, str]:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self.config.buywell_url}/api/v2/edge/pair",
                json={
                    "code": code.strip().upper(),
                    "name": name,
                    "version": __version__,
                    "platform": self.config.platform_name,
                },
            )
        response.raise_for_status()
        value = response.json()
        credential_reference = "device:credential"
        self.vault.put(credential_reference, {"credential": value["credential"]})
        self.store.set_metadata("device_id", value["deviceId"])
        self.store.set_metadata("device_credential_ref", credential_reference)
        self.store.set_metadata("buywell_url", self.config.buywell_url)
        self.store.set_metadata("locale", "en" if value.get("locale") == "en" else "ru")
        return value["deviceId"], value["credential"]

    async def run(self) -> None:
        delay = 1.0
        while not self._stop.is_set():
            session = asyncio.create_task(self._session())
            stopping = asyncio.create_task(self._stop.wait())
            try:
                done, _ = await asyncio.wait(
                    {session, stopping},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if stopping in done:
                    session.cancel()
                    await asyncio.gather(session, return_exceptions=True)
                    return
                stopping.cancel()
                await asyncio.gather(stopping, return_exceptions=True)
                await session
                delay = 1.0
            except asyncio.CancelledError:
                session.cancel()
                stopping.cancel()
                await asyncio.gather(session, stopping, return_exceptions=True)
                raise
            except Exception:
                stopping.cancel()
                await asyncio.gather(stopping, return_exceptions=True)
                try:
                    await asyncio.wait_for(
                        self._stop.wait(),
                        timeout=delay + random.random(),
                    )
                    return
                except TimeoutError:
                    pass
                delay = min(self.config.reconnect_max_seconds, delay * 2)

    async def _session(self) -> None:
        device_id = self.store.metadata("device_id")
        credential_reference = self.store.metadata("device_credential_ref")
        credential = self.vault.get(credential_reference).get("credential")
        legacy_credential = self.store.metadata("device_credential")
        if not credential and legacy_credential:
            credential_reference = "device:credential"
            self.vault.put(credential_reference, {"credential": legacy_credential})
            self.store.set_metadata("device_credential_ref", credential_reference)
            self.store.delete_metadata("device_credential")
            credential = legacy_credential
        if not device_id or not credential:
            raise RuntimeError("Edge is not paired")
        socket_url = self.config.buywell_url.replace("https://", "wss://").replace("http://", "ws://") + "/api/v2/edge/socket"
        async with connect(socket_url, max_size=2 * 1024 * 1024, ping_interval=None) as socket:
            await socket.send(json.dumps({
                "type": "authenticate",
                "protocolVersion": EDGE_PROTOCOL_VERSION,
                "deviceId": device_id,
                "credential": credential,
                "edgeVersion": __version__,
                "platform": self.config.platform_name,
                "hostname": platform.node()[:120],
            }))
            authenticated = json.loads(await asyncio.wait_for(socket.recv(), 15))
            if authenticated.get("type") != "authenticated":
                raise RuntimeError("Edge authentication failed")
            if authenticated.get("locale") in {"ru", "en"}:
                self.store.set_metadata("locale", authenticated["locale"])
            await self.handler({"type": "gateway.connected"})

            async def heartbeat() -> None:
                while True:
                    await asyncio.sleep(self.config.heartbeat_seconds)
                    snapshot = await self.handler({"type": "connection.sync", "requestId": None})
                    await socket.send(json.dumps({
                        "type": "heartbeat",
                        "connections": snapshot.get("connections", []) if snapshot else [],
                    }, ensure_ascii=False, separators=(",", ":")))

            task = asyncio.create_task(heartbeat())
            async def send_outbound() -> None:
                while True:
                    payload = await self._outbound.get()
                    try:
                        await socket.send(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
                    except Exception:
                        await self._outbound.put(payload)
                        raise
                    finally:
                        self._outbound.task_done()
            sender = asyncio.create_task(send_outbound())
            try:
                async for raw in socket:
                    message = json.loads(raw)
                    response = await self.handler(message)
                    if response is not None:
                        await socket.send(json.dumps(response, ensure_ascii=False, separators=(",", ":")))
            finally:
                task.cancel()
                sender.cancel()
                await asyncio.gather(task, sender, return_exceptions=True)

    def stop(self) -> None:
        self._stop.set()
