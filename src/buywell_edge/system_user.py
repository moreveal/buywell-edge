from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import NoReturn

try:
    import pwd
except ImportError:  # pragma: no cover - Windows has no pwd module.
    pwd = None  # type: ignore[assignment]


SERVICE_USER = "buywell-edge"
SYSTEM_STATE_DIRECTORY = Path("/var/lib/buywell-edge")


def should_use_service_user(arguments: list[str], state_directory: Path) -> bool:
    if os.name == "nt" or os.geteuid() != 0:
        return False
    if state_directory.resolve() != SYSTEM_STATE_DIRECTORY:
        return False
    if not arguments or arguments[0].startswith("-"):
        return False
    if arguments[0] in {"version", "update", "rollback", "logs"}:
        return False
    return arguments[:2] != ["module", "build"]


def run_as_service_user(arguments: list[str] | None = None) -> NoReturn:
    command_arguments = list(sys.argv[1:] if arguments is None else arguments)
    if pwd is None:
        raise RuntimeError("The Buywell Edge service user is unavailable")
    pwd.getpwnam(SERVICE_USER)
    executable = [sys.executable]
    if not getattr(sys, "frozen", False):
        executable.append(sys.argv[0])
    os.execvp(
        "runuser",
        [
            "runuser",
            "-u",
            SERVICE_USER,
            "--",
            *executable,
            *command_arguments,
        ],
    )
    raise AssertionError("os.execvp returned unexpectedly")


def repair_state_ownership(state_directory: Path) -> None:
    if os.name == "nt" or os.geteuid() != 0 or not state_directory.exists():
        return
    if pwd is None:
        raise RuntimeError("The Buywell Edge service user is unavailable")
    account = pwd.getpwnam(SERVICE_USER)
    paths = [state_directory]
    for root, directories, files in os.walk(state_directory, followlinks=False):
        paths.extend(Path(root) / name for name in directories)
        paths.extend(Path(root) / name for name in files)
    for path in paths:
        os.chown(path, account.pw_uid, account.pw_gid, follow_symlinks=False)
