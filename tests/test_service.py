from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from buywell_edge.service import EdgeService


@pytest.mark.asyncio
async def test_live_extension_event_uses_the_same_canonical_payload_as_retry() -> None:
    stored: dict[str, object] = {}
    store = SimpleNamespace()

    def enqueue_event(connection_id: str, payload: dict[str, object]) -> str:
        stored["connection_id"] = connection_id
        stored["payload"] = payload
        return "purchase:one"

    store.enqueue_event = Mock(side_effect=enqueue_event)
    gateway = SimpleNamespace(send=AsyncMock())
    service = SimpleNamespace(store=store, gateway=gateway)
    process = SimpleNamespace(
        instance_id="instance-one",
        connection=SimpleNamespace(
            id="connection-one",
            extension_id="steamshop.local",
            extension_version="1.0.2",
            package_digest="a" * 64,
        ),
    )

    await EdgeService.handle_extension_event(service, process, {
        "type": "event",
        "eventType": "commerce.purchase.created",
        "eventVersion": "1.2.0",
        "eventId": "purchase:one",
        "payload": {"orderId": "one"},
        "scope": {"orderId": "one"},
    })

    persisted = stored["payload"]
    assert isinstance(persisted, dict)
    assert "type" not in persisted
    gateway.send.assert_awaited_once_with({
        "type": "event",
        "event": persisted,
    })


@pytest.mark.asyncio
async def test_accepted_heartbeat_reoffers_due_durable_events() -> None:
    service = SimpleNamespace(resend_events=AsyncMock())

    result = await EdgeService.handle_gateway_message(
        service,
        {"type": "heartbeat.accepted"},
    )

    assert result is None
    service.resend_events.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_rejected_event_is_not_acknowledged(caplog: pytest.LogCaptureFixture) -> None:
    store = SimpleNamespace(acknowledge_event=Mock())
    service = SimpleNamespace(store=store)

    result = await EdgeService.handle_gateway_message(service, {
        "type": "event.rejected",
        "eventId": "purchase:one",
        "response": {
            "error": {
                "code": "INVALID_SCHEMA",
                "message": "Request validation failed",
            },
        },
    })

    assert result is None
    store.acknowledge_event.assert_not_called()
    assert "INVALID_SCHEMA" in caplog.text
