from __future__ import annotations

import runpy
import sys
import os


def _enable_frozen_pip_resources() -> None:
    """Teach distlib how to read data collected by PyInstaller."""
    if not getattr(sys, "frozen", False):
        return
    try:
        import pip._vendor.distlib as distlib
        from pip._vendor.distlib import resources
    except ImportError:
        return
    loader = getattr(distlib, "__loader__", None)
    if loader is not None:
        resources.register_finder(loader, resources.ResourceFinder)


def _python_compatibility_mode() -> bool:
    """Let the frozen Edge binary host pip and extension subprocesses."""
    if getattr(sys, "frozen", False):
        for path in reversed(os.environ.get("PYTHONPATH", "").split(os.pathsep)):
            if path and path not in sys.path:
                sys.path.insert(0, path)
    if len(sys.argv) >= 3 and sys.argv[1] == "-m":
        module = sys.argv[2]
        if module == "pip" or module.startswith("pip."):
            _enable_frozen_pip_resources()
        sys.argv = [module, *sys.argv[3:]]
        runpy.run_module(module, run_name="__main__", alter_sys=True)
        return True
    if len(sys.argv) >= 3 and sys.argv[1] == "-c":
        source = sys.argv[2]
        sys.argv = ["-c", *sys.argv[3:]]
        namespace = {"__name__": "__main__", "__builtins__": __builtins__}
        exec(compile(source, "<edge-command>", "exec"), namespace, namespace)
        return True
    return False


if __name__ == "__main__":
    if not _python_compatibility_mode():
        from buywell_edge.cli import app

        app()
