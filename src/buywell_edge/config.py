from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from pathlib import Path


def default_state_directory() -> Path:
    override = os.environ.get("BUYWELL_EDGE_STATE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        return Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Buywell" / "Edge"
    if os.geteuid() == 0:
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
        return cls(
            state_directory=default_state_directory(),
            install_directory=default_install_directory(),
            buywell_url=os.environ.get("BUYWELL_URL", "https://buywell.pro").rstrip("/"),
            developer_mode=os.environ.get("BUYWELL_EDGE_DEVELOPER_MODE", "").lower() in {"1", "true", "yes"},
        )

    @property
    def platform_name(self) -> str:
        return f"{platform.system().lower()}-{platform.machine().lower()}"
