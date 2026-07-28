from __future__ import annotations

import getpass
import os
import platform
import sqlite3
from dataclasses import dataclass
from pathlib import Path


def default_state_directory() -> Path:
    override = os.environ.get("BUYWELL_EDGE_STATE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        return Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Buywell" / "Edge"
    if os.geteuid() == 0 or getpass.getuser() == "buywell-edge":
        return Path("/var/lib/buywell-edge")
    return Path.home() / ".local" / "share" / "buywell-edge"


def default_install_directory() -> Path:
    override = os.environ.get("BUYWELL_EDGE_INSTALL_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        return Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Buywell Edge"
    return Path("/opt/buywell-edge")


@dataclass(frozen=True)
class EdgeConfig:
    state_directory: Path
    install_directory: Path
    buywell_url: str
    developer_mode: bool = False
    heartbeat_seconds: int = 30
    reconnect_max_seconds: int = 30

    @classmethod
    def load(cls) -> "EdgeConfig":
        state_directory = default_state_directory()
        buywell_url = os.environ.get("BUYWELL_URL") or _stored_buywell_url(
            state_directory / "edge.sqlite3"
        ) or "https://buywell.pro"
        return cls(
            state_directory=state_directory,
            install_directory=default_install_directory(),
            buywell_url=buywell_url.rstrip("/"),
            developer_mode=os.environ.get("BUYWELL_EDGE_DEVELOPER_MODE", "").lower() in {"1", "true", "yes"},
        )

    @property
    def platform_name(self) -> str:
        return f"{platform.system().lower()}-{platform.machine().lower()}"


def _stored_buywell_url(database_path: Path) -> str | None:
    if not database_path.is_file():
        return None
    try:
        with sqlite3.connect(database_path) as database:
            row = database.execute(
                "SELECT value FROM metadata WHERE key='buywell_url'"
            ).fetchone()
    except sqlite3.Error:
        return None
    value = str(row[0]).strip() if row else ""
    return value if value.startswith(("http://", "https://")) else None
