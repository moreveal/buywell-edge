import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from buywell_edge.cli import app
from buywell_edge.config import EdgeConfig
from buywell_edge.service import EdgeService
from buywell_edge.storage import ConnectionRecord


def manifest() -> dict:
    return {
        "extension": {
            "id": "funpay.cardinal",
            "version": "1.3.0",
            "kind": "module",
            "displayName": {"ru": "FunPay", "en": "FunPay"},
        },
        "package": {"digest": "a" * 64},
        "configuration": {
            "schema": {
                "type": "object",
                "properties": {
                    "golden_key": {"type": "string", "title": "Golden Key"},
                    "poll_interval_seconds": {
                        "type": "number",
                        "title": "Poll interval",
                        "default": 6,
                    },
                },
                "required": ["golden_key"],
            },
            "secretFields": ["golden_key"],
        },
    }


def service(tmp_path: Path) -> EdgeService:
    value = EdgeService(EdgeConfig(
        state_directory=tmp_path / "state",
        install_directory=tmp_path / "install",
        buywell_url="https://buywell.pro",
    ))
    value.store.register_package(manifest(), tmp_path / "package")
    value.store.set_metadata("locale", "ru")
    return value


def test_wizard_selects_digest_and_supports_multiple_accounts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    edge = service(tmp_path)
    monkeypatch.setattr("buywell_edge.cli._service", lambda: edge)
    runner = CliRunner()

    first = runner.invoke(
        app,
        ["connection", "add", "funpay.cardinal"],
        input="\n\nsecret-one\n",
    )
    second = runner.invoke(
        app,
        ["connection", "add", "funpay.cardinal"],
        input="\n\nsecret-two\n",
    )

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert "Аккаунт подключён" in first.output
    connections = edge.store.connections()
    assert [item.display_name for item in connections] == ["FunPay", "FunPay 2"]
    assert {item.package_digest for item in connections} == {"a" * 64}
    assert len({item.secret_ref for item in connections}) == 2
    assert {
        edge.vault.get(item.secret_ref).get("golden_key")
        for item in connections
    } == {"secret-one", "secret-two"}

    status = runner.invoke(app, ["connection", "status", "FunPay 2"])
    assert status.exit_code == 0, status.output
    assert '"name": "FunPay 2"' in status.output


def test_status_needs_no_hidden_id_for_single_connection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    edge = service(tmp_path)
    edge.store.upsert_connection(ConnectionRecord(
        id="hidden-connection-id",
        extension_id="funpay.cardinal",
        extension_version="1.3.0",
        package_digest="a" * 64,
        display_name="Irohazaka",
        kind="module",
        enabled=True,
        config={},
        secret_ref=None,
        health_state="degraded",
        health_message="Connecting to FunPay",
        session_expires_at=None,
        last_success_at=None,
    ))
    monkeypatch.setattr("buywell_edge.cli._service", lambda: edge)

    result = CliRunner().invoke(app, ["connection", "status"])

    assert result.exit_code == 0, result.output
    assert '"name": "Irohazaka"' in result.output
    assert '"message": "Connecting to FunPay"' in result.output


@pytest.mark.asyncio
async def test_service_starts_new_connection_and_publishes_healthy_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    edge = service(tmp_path)
    edge.store.upsert_connection(ConnectionRecord(
        id="connection-1",
        extension_id="funpay.cardinal",
        extension_version="1.3.0",
        package_digest="a" * 64,
        display_name="FunPay",
        kind="module",
        enabled=True,
        config={},
        secret_ref=None,
        health_state="offline",
        health_message=None,
        session_expires_at=None,
        last_success_at=None,
    ))
    sent = []

    class Supervisor:
        processes = {}

        async def start(self, connection):
            self.processes[connection.id] = SimpleNamespace(
                connection=connection,
                instance_id="instance-1",
            )

        async def health(self, connection_id):
            edge.store.update_health(connection_id, {"state": "healthy"})

        async def stop(self, connection_id):
            self.processes.pop(connection_id, None)

    edge.supervisor = Supervisor()
    edge.gateway = SimpleNamespace(send=lambda payload: _capture(sent, payload))

    async def stop_after_first_iteration(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "sleep", stop_after_first_iteration)

    with pytest.raises(asyncio.CancelledError):
        await edge.health_loop()

    assert sent[0]["connections"][0]["health"]["state"] == "healthy"
    assert sent[0]["connections"][0]["instanceId"] == "instance-1"


async def _capture(target, payload):
    target.append(payload)
