import asyncio
import copy
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
                    "golden_key": {
                        "type": "string",
                        "x-buywell-label": {"ru": "Ключ FunPay", "en": "FunPay key"},
                    },
                    "user_agent": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "default": None,
                        "x-buywell-label": {"ru": "User-Agent браузера", "en": "Browser User-Agent"},
                    },
                    "poll_interval_seconds": {
                        "type": "number",
                        "x-buywell-label": {"ru": "Интервал проверки", "en": "Poll interval"},
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
        input="\n\n\nsecret-one\n",
    )
    second = runner.invoke(
        app,
        ["connection", "add", "funpay.cardinal"],
        input="\n\n\nsecret-two\n",
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


def test_login_prompts_for_module_fields_and_keeps_skipped_values(
    tmp_path: Path,
    monkeypatch,
) -> None:
    edge = service(tmp_path)
    edge.store.upsert_connection(ConnectionRecord(
        id="connection-1",
        extension_id="funpay.cardinal",
        extension_version="1.3.0",
        package_digest="a" * 64,
        display_name="Irohazaka",
        kind="module",
        enabled=True,
        config={"user_agent": "old-agent", "poll_interval_seconds": 6},
        secret_ref="connection:1",
        health_state="auth_required",
        health_message="Sign in again",
        session_expires_at=None,
        last_success_at=None,
    ))
    edge.vault.put("connection:1", {"golden_key": "old-key"})
    monkeypatch.setattr("buywell_edge.cli._service", lambda: edge)

    kept = CliRunner().invoke(
        app,
        ["connection", "login", "Irohazaka"],
        input="\n\n\n",
    )

    assert kept.exit_code == 0, kept.output
    assert "User-Agent браузера" in kept.output
    selected = edge.store.connections()[0]
    assert selected.config["user_agent"] == "old-agent"
    assert edge.vault.get(selected.secret_ref)["golden_key"] == "old-key"

    changed = CliRunner().invoke(
        app,
        ["connection", "login", "Irohazaka"],
        input="new-agent\n\nnew-key\n",
    )
    assert changed.exit_code == 0, changed.output
    selected = edge.store.connections()[0]
    assert selected.config["user_agent"] == "new-agent"
    assert edge.vault.get(selected.secret_ref)["golden_key"] == "new-key"


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


def test_lists_and_switches_modules_without_internal_identifiers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    edge = service(tmp_path)
    newer = copy.deepcopy(manifest())
    newer["extension"]["version"] = "1.4.0"
    newer["package"]["digest"] = "b" * 64
    package = tmp_path / "package-new"
    package.mkdir()
    edge.store.register_package(newer, package)
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
        health_state="healthy",
        health_message=None,
        session_expires_at=None,
        last_success_at=None,
    ))
    monkeypatch.setattr("buywell_edge.cli._service", lambda: edge)
    runner = CliRunner()

    connections = runner.invoke(app, ["connection", "list"])
    modules = runner.invoke(app, ["module", "list"])
    switched = runner.invoke(app, ["module", "switch", "Irohazaka", "1.4.0"])

    assert connections.exit_code == 0, connections.output
    assert "Irohazaka" in connections.output
    assert "funpay.cardinal" in connections.output
    assert modules.exit_code == 0, modules.output
    assert "1.3.0" in modules.output
    assert "1.4.0" in modules.output
    assert switched.exit_code == 0, switched.output
    selected = edge.store.connections()[0]
    assert selected.extension_version == "1.4.0"
    assert selected.package_digest == "b" * 64


def test_removes_connection_and_its_secrets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    edge = service(tmp_path)
    edge.store.upsert_connection(ConnectionRecord(
        id="connection-1",
        extension_id="funpay.cardinal",
        extension_version="1.3.0",
        package_digest="a" * 64,
        display_name="Irohazaka",
        kind="module",
        enabled=True,
        config={},
        secret_ref="connection:1",
        health_state="offline",
        health_message=None,
        session_expires_at=None,
        last_success_at=None,
    ))
    edge.vault.put("connection:1", {"golden_key": "secret"})
    monkeypatch.setattr("buywell_edge.cli._service", lambda: edge)

    result = CliRunner().invoke(app, ["connection", "remove", "Irohazaka", "--yes"])

    assert result.exit_code == 0, result.output
    assert edge.store.connections() == []
    assert edge.vault.get("connection:1") == {}


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


@pytest.mark.asyncio
async def test_service_rechecks_degraded_connection_without_waiting_for_slow_poll(
    tmp_path: Path,
    monkeypatch,
) -> None:
    edge = service(tmp_path)
    connection = ConnectionRecord(
        id="connection-1",
        extension_id="funpay.cardinal",
        extension_version="1.3.0",
        package_digest="a" * 64,
        display_name="FunPay",
        kind="module",
        enabled=True,
        config={},
        secret_ref=None,
        health_state="degraded",
        health_message="Connecting to FunPay",
        session_expires_at=None,
        last_success_at=None,
    )
    edge.store.upsert_connection(connection)
    sent = []

    class Supervisor:
        processes = {
            connection.id: SimpleNamespace(
                connection=connection,
                instance_id="instance-1",
            )
        }

        async def health(self, connection_id):
            edge.store.update_health(connection_id, {"state": "healthy"})

        async def stop(self, connection_id):
            raise AssertionError("Healthy process must not be restarted")

    edge.supervisor = Supervisor()
    edge.gateway = SimpleNamespace(send=lambda payload: _capture(sent, payload))

    async def stop_after_first_iteration(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "sleep", stop_after_first_iteration)

    with pytest.raises(asyncio.CancelledError):
        await edge.health_loop()

    assert edge.store.connections()[0].health_state == "healthy"
    assert sent[0]["connections"][0]["health"]["state"] == "healthy"


async def _capture(target, payload):
    target.append(payload)
