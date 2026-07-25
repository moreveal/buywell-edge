from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from buywell_edge.config import EdgeConfig
from buywell_edge.gateway import GatewayClient


@pytest.mark.asyncio
async def test_stop_interrupts_an_active_gateway_session(tmp_path: Path) -> None:
    config = EdgeConfig(
        state_directory=tmp_path,
        install_directory=tmp_path,
        buywell_url="https://example.invalid",
    )
    client = GatewayClient(config, object(), object(), lambda _: None)  # type: ignore[arg-type]
    session_started = asyncio.Event()

    async def blocked_session() -> None:
        session_started.set()
        await asyncio.Event().wait()

    client._session = blocked_session  # type: ignore[method-assign]
    running = asyncio.create_task(client.run())
    await session_started.wait()

    client.stop()
    await asyncio.wait_for(running, timeout=0.5)


@pytest.mark.asyncio
async def test_stop_interrupts_reconnect_backoff(tmp_path: Path) -> None:
    config = EdgeConfig(
        state_directory=tmp_path,
        install_directory=tmp_path,
        buywell_url="https://example.invalid",
        reconnect_max_seconds=30,
    )
    client = GatewayClient(config, object(), object(), lambda _: None)  # type: ignore[arg-type]
    attempted = asyncio.Event()

    async def failed_session() -> None:
        attempted.set()
        raise ConnectionError

    client._session = failed_session  # type: ignore[method-assign]
    running = asyncio.create_task(client.run())
    await attempted.wait()

    client.stop()
    await asyncio.wait_for(running, timeout=0.5)
