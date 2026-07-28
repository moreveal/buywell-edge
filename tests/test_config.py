from pathlib import Path

from buywell_edge.config import EdgeConfig
from buywell_edge.storage import EdgeStore


def test_saved_gateway_survives_restart(monkeypatch, tmp_path: Path) -> None:
    state = tmp_path / "state"
    monkeypatch.setenv("BUYWELL_EDGE_STATE_DIR", str(state))
    monkeypatch.delenv("BUYWELL_URL", raising=False)
    EdgeStore(state / "edge.sqlite3").set_metadata(
        "buywell_url", "http://localhost:3000"
    )

    assert EdgeConfig.load().buywell_url == "http://localhost:3000"


def test_gateway_environment_overrides_saved_value(monkeypatch, tmp_path: Path) -> None:
    state = tmp_path / "state"
    monkeypatch.setenv("BUYWELL_EDGE_STATE_DIR", str(state))
    EdgeStore(state / "edge.sqlite3").set_metadata(
        "buywell_url", "http://localhost:3000"
    )
    monkeypatch.setenv("BUYWELL_URL", "https://staging.buywell.test/")

    assert EdgeConfig.load().buywell_url == "https://staging.buywell.test"
