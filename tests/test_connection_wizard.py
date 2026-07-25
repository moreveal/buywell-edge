from pathlib import Path

from typer.testing import CliRunner

from buywell_edge.cli import app
from buywell_edge.config import EdgeConfig
from buywell_edge.service import EdgeService


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
