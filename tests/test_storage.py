from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from buywell_edge.config import EdgeConfig
from buywell_edge.secrets import SecretVault
from buywell_edge.service import EdgeService
from buywell_edge.storage import ConnectionRecord, EdgeStore
from buywell_edge.updater import ReleaseManager


def record() -> ConnectionRecord:
    return ConnectionRecord(
        id="connection-1",
        extension_id="example.market",
        extension_version="1.0.0",
        package_digest="a" * 64,
        display_name="Example",
        kind="module",
        enabled=True,
        config={"prefix": "Hello"},
        secret_ref="connection:1",
        health_state="offline",
        health_message=None,
        session_expires_at=None,
        last_success_at=None,
    )


def test_connection_and_idempotency_are_durable(tmp_path: Path):
    store = EdgeStore(tmp_path / "edge.sqlite3")
    store.upsert_connection(record())
    assert store.connections()[0].config == {"prefix": "Hello"}
    assert store.begin_idempotent("operation")
    assert not store.begin_idempotent("operation")
    store.finish_idempotent("operation", "success", {"value": 1})
    assert store.idempotency_result("operation") == {"status": "success", "value": {"value": 1}}


def test_switch_preserves_health_until_atomic_restart(tmp_path: Path):
    store = EdgeStore(tmp_path / "edge.sqlite3")
    current = record()
    store.upsert_connection(ConnectionRecord(
        **{
            **current.__dict__,
            "health_state": "healthy",
            "last_success_at": "2026-07-25T12:00:00+00:00",
        }
    ))
    target_manifest = {
        "extension": {"id": "example.market", "version": "1.1.0"},
        "package": {"digest": "b" * 64},
    }
    target = tmp_path / "target"
    target.mkdir()
    store.register_package(target_manifest, target)

    store.switch_connection("connection-1", "1.1.0", "b" * 64)

    switched = store.connections()[0]
    assert switched.extension_version == "1.1.0"
    assert switched.health_state == "healthy"
    assert switched.last_success_at == "2026-07-25T12:00:00+00:00"


def test_package_cleanup_is_durable(tmp_path: Path):
    store = EdgeStore(tmp_path / "edge.sqlite3")
    store.schedule_package_cleanup(
        "connection-1",
        "example.market",
        "1.0.0",
        "a" * 64,
    )
    assert store.pending_package_cleanup("connection-1") == {
        "extensionId": "example.market",
        "version": "1.0.0",
        "digest": "a" * 64,
    }
    store.finish_package_cleanup("connection-1")
    assert store.pending_package_cleanup("connection-1") is None


def test_snapshot_reports_running_version_during_atomic_cutover(tmp_path: Path):
    service = EdgeService(EdgeConfig(
        state_directory=tmp_path / "state",
        install_directory=tmp_path / "install",
        buywell_url="https://buywell.pro",
    ))
    old = record()
    service.store.upsert_connection(old)
    old_directory = tmp_path / "old"
    old_directory.mkdir()
    service.store.register_package({
        "extension": {"id": "example.market", "version": "1.0.0"},
        "package": {"digest": "a" * 64},
    }, old_directory)
    new_directory = tmp_path / "new"
    new_directory.mkdir()
    service.store.register_package({
        "extension": {"id": "example.market", "version": "1.1.0"},
        "package": {"digest": "b" * 64},
    }, new_directory)
    service.store.switch_connection("connection-1", "1.1.0", "b" * 64)
    service.supervisor.processes["connection-1"] = SimpleNamespace(
        instance_id="instance-old",
        connection=old,
    )

    snapshot = service.connection_snapshot()["connections"][0]

    assert snapshot["extensionVersion"] == "1.0.0"
    assert snapshot["packageDigest"] == "a" * 64
    assert snapshot["instanceId"] == "instance-old"


def test_vault_encrypts_values_at_rest(tmp_path: Path):
    vault = SecretVault(tmp_path)
    vault.put("connection:1", {"token": "secret-value"})
    assert vault.get("connection:1") == {"token": "secret-value"}
    assert b"secret-value" not in (tmp_path / "vault.json").read_bytes()


def test_release_pointer_rolls_back(tmp_path: Path):
    manager = ReleaseManager(tmp_path)
    (manager.releases / "1.0.0").mkdir()
    (manager.releases / "1.1.0").mkdir()
    assert manager.switch("1.0.0") is None
    assert manager.switch("1.1.0") == "1.0.0"
    assert manager.rollback() == "1.0.0"


def test_release_pruning_preserves_selected_versions(tmp_path: Path):
    manager = ReleaseManager(tmp_path)
    for version in ("1.0.0", "1.1.0", "1.2.0"):
        (manager.releases / version).mkdir()
    manager.prune({"1.1.0", "1.2.0"})
    assert {path.name for path in manager.releases.iterdir()} == {"1.1.0", "1.2.0"}


def test_event_outbox_survives_until_acknowledged(tmp_path: Path):
    store = EdgeStore(tmp_path / "edge.sqlite3")
    store.upsert_connection(record())
    event_id = store.enqueue_event("connection-1", {"eventId": "event-1", "payload": {"value": 1}})
    assert event_id == "event-1"
    pending = store.pending_events()
    assert pending[0][2]["payload"] == {"value": 1}
    store.acknowledge_event(event_id)
    assert store.pending_events() == []


def test_metadata_can_be_removed_after_secret_migration(tmp_path: Path):
    store = EdgeStore(tmp_path / "edge.sqlite3")
    store.set_metadata("device_credential", "legacy-secret")
    store.delete_metadata("device_credential")
    assert store.metadata("device_credential") is None
