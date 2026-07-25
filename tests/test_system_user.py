from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from buywell_edge import system_user


def emulate_root_service_account(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(system_user.os, "name", "posix")
    monkeypatch.setattr(system_user.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(
        system_user,
        "pwd",
        SimpleNamespace(
            getpwnam=lambda _: SimpleNamespace(pw_uid=123, pw_gid=456),
        ),
    )


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (["status"], True),
        (["connect", "BW-ABCDEFGH"], True),
        (["module", "install", "adapter.ns-gifts@1.0.4"], True),
        (["connection", "add", "adapter.ns-gifts"], True),
        (["module", "build", "package:definition"], False),
        (["update"], False),
        (["rollback"], False),
        (["version"], False),
        (["--help"], False),
    ],
)
@pytest.mark.skipif(os.name == "nt", reason="Linux service account behavior")
def test_root_system_commands_select_service_user(
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
    expected: bool,
) -> None:
    emulate_root_service_account(monkeypatch)
    assert system_user.should_use_service_user(
        arguments,
        Path("/var/lib/buywell-edge"),
    ) is expected


@pytest.mark.skipif(os.name == "nt", reason="Linux service account behavior")
def test_custom_state_directory_stays_with_invoking_user(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    emulate_root_service_account(monkeypatch)
    assert not system_user.should_use_service_user(["status"], tmp_path)


@pytest.mark.skipif(os.name == "nt", reason="Linux service account behavior")
def test_frozen_cli_reexecutes_without_duplicate_executable_argument(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command: list[str] = []
    emulate_root_service_account(monkeypatch)
    monkeypatch.setattr(system_user.sys, "frozen", True, raising=False)
    monkeypatch.setattr(system_user.sys, "executable", "/opt/buywell-edge/bin/buywell-edge")
    monkeypatch.setattr(
        system_user.os,
        "execvp",
        lambda _, arguments: command.extend(arguments),
    )

    with pytest.raises(AssertionError):
        system_user.run_as_service_user(["status"])

    assert command == [
        "runuser",
        "-u",
        "buywell-edge",
        "--",
        "/opt/buywell-edge/bin/buywell-edge",
        "status",
    ]


@pytest.mark.skipif(os.name == "nt", reason="Linux ownership repair")
def test_repair_state_ownership_includes_locked_package_directories(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    package = tmp_path / "packages" / "adapter.ns-gifts" / "1.0.1" / "digest"
    dependencies = package / "dependencies"
    dependencies.mkdir(parents=True)
    package.chmod(0o700)
    changed: list[Path] = []
    emulate_root_service_account(monkeypatch)
    monkeypatch.setattr(
        system_user.os,
        "chown",
        lambda path, uid, gid, follow_symlinks: changed.append(Path(path)),
    )

    system_user.repair_state_ownership(tmp_path)

    assert package in changed
    assert dependencies in changed
