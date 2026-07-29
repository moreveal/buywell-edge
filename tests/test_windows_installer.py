from __future__ import annotations

from pathlib import Path


def test_installer_registers_service_dispatcher_and_checks_failures() -> None:
    script = Path("install/install.ps1").read_text("utf-8")

    assert "service-install" in script
    assert "service-start" in script
    assert "$LASTEXITCODE -ne 0" in script
    assert 'binPath= "`"$executable`" run"' not in script


def test_installer_stops_existing_service_before_switching_current() -> None:
    script = Path("install/install.ps1").read_text("utf-8")

    stop = script.index("Stop-Service -Name BuywellEdge")
    switch = script.index("[System.IO.Directory]::Delete($current)")
    assert stop < switch


def test_installer_does_not_follow_current_junction_during_switch() -> None:
    script = Path("install/install.ps1").read_text("utf-8")

    assert "Remove-Item -Force -LiteralPath $current" not in script
    assert "[System.IO.Directory]::Delete($current)" in script
