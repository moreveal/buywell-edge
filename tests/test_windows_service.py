from __future__ import annotations

import subprocess
from pathlib import Path

from buywell_edge import windows_service


def completed(returncode: int = 0, stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, "")


def test_configure_creates_real_service_dispatcher_command(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(windows_service, "_require_windows", lambda: None)

    def sc(*arguments: str, check: bool = True):
        calls.append(arguments)
        if arguments[:2] == ("query", "BuywellEdge"):
            return completed(1060)
        return completed()

    monkeypatch.setattr(windows_service, "_sc", sc)

    windows_service.configure_service(
        Path(r"C:\Program Files\Buywell Edge\current\buywell-edge.exe")
    )

    assert calls[1] == (
        "create",
        "BuywellEdge",
        "binPath=",
        '"C:\\Program Files\\Buywell Edge\\current\\buywell-edge.exe" service-run',
        "start=",
        "auto",
    )


def test_configure_repairs_existing_service_in_place(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(windows_service, "_require_windows", lambda: None)

    def sc(*arguments: str, check: bool = True):
        calls.append(arguments)
        return completed()

    monkeypatch.setattr(windows_service, "_sc", sc)

    windows_service.configure_service(Path(r"C:\Edge\buywell-edge.exe"))

    assert calls[1][:2] == ("config", "BuywellEdge")
    assert not any(call[0] == "create" for call in calls)


def test_start_is_idempotent_when_service_is_running(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(windows_service, "_require_windows", lambda: None)

    def sc(*arguments: str, check: bool = True):
        calls.append(arguments)
        return completed(stdout="STATE : 4 RUNNING")

    monkeypatch.setattr(windows_service, "_sc", sc)

    windows_service.start_service()

    assert calls == [("query", "BuywellEdge")]
