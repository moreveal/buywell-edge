from __future__ import annotations

import asyncio
import os
import re
import subprocess
import threading
import time
from pathlib import Path

from .config import EdgeConfig
from .service import EdgeService


SERVICE_NAME = "BuywellEdge"


def _require_windows() -> None:
    if os.name != "nt":
        raise RuntimeError("Windows service commands are only available on Windows")


def _sc(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["sc.exe", *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(detail or f"sc.exe {' '.join(arguments)} failed")
    return result


def service_exists() -> bool:
    _require_windows()
    return _sc("query", SERVICE_NAME, check=False).returncode == 0


def service_state() -> str | None:
    _require_windows()
    result = _sc("query", SERVICE_NAME, check=False)
    if result.returncode != 0:
        return None
    match = re.search(r":\s*([1-7])\s", result.stdout)
    if match:
        return {
            "1": "STOPPED",
            "2": "START_PENDING",
            "3": "STOP_PENDING",
            "4": "RUNNING",
            "5": "CONTINUE_PENDING",
            "6": "PAUSE_PENDING",
            "7": "PAUSED",
        }[match.group(1)]
    return "UNKNOWN"


def configure_service(executable: Path) -> None:
    _require_windows()
    command = f'"{executable}" service-run'
    if service_exists():
        _sc("config", SERVICE_NAME, "binPath=", command, "start=", "auto")
    else:
        _sc("create", SERVICE_NAME, "binPath=", command, "start=", "auto")
    _sc("description", SERVICE_NAME, "Buywell Edge local integration runtime")
    _sc(
        "failure",
        SERVICE_NAME,
        "reset=",
        "86400",
        "actions=",
        "restart/5000/restart/30000",
    )


def stop_service() -> None:
    _require_windows()
    state = service_state()
    if state is None or state == "STOPPED":
        return
    _sc("stop", SERVICE_NAME, check=False)
    for _ in range(80):
        if service_state() in {None, "STOPPED"}:
            return
        time.sleep(0.25)
    raise RuntimeError("Buywell Edge service did not stop")


def start_service() -> None:
    _require_windows()
    if service_state() == "RUNNING":
        return
    _sc("start", SERVICE_NAME)
    for _ in range(80):
        state = service_state()
        if state == "RUNNING":
            return
        if state == "STOPPED":
            raise RuntimeError("Buywell Edge service stopped during startup")
        time.sleep(0.25)
    raise RuntimeError("Buywell Edge service did not start")


def run_service_dispatcher() -> None:
    _require_windows()
    import servicemanager
    import win32service
    import win32serviceutil

    class BuywellEdgeService(win32serviceutil.ServiceFramework):
        _svc_name_ = SERVICE_NAME
        _svc_display_name_ = "Buywell Edge"
        _svc_description_ = "Buywell Edge local integration runtime"

        def __init__(self, args):
            super().__init__(args)
            self._loop: asyncio.AbstractEventLoop | None = None
            self._edge: EdgeService | None = None
            self._stop_requested = threading.Event()

        def SvcStop(self) -> None:
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            self._stop_requested.set()
            if self._loop and self._edge:
                self._loop.call_soon_threadsafe(self._edge.gateway.stop)

        def SvcDoRun(self) -> None:
            async def run() -> None:
                self._loop = asyncio.get_running_loop()
                self._edge = EdgeService(EdgeConfig.load())
                if self._stop_requested.is_set():
                    self._edge.gateway.stop()
                await self._edge.run()

            asyncio.run(run())

    servicemanager.Initialize()
    servicemanager.PrepareToHostSingle(BuywellEdgeService)
    servicemanager.StartServiceCtrlDispatcher()
