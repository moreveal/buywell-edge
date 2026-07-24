from __future__ import annotations

import json
import os
import shutil
import tempfile
import tarfile
import zipfile
from pathlib import Path


class ReleaseManager:
    """A/B release pointer used by platform service wrappers."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.releases = root / "releases"
        self.pointer = root / "current.json"
        self.releases.mkdir(parents=True, exist_ok=True)

    def current(self) -> str | None:
        return json.loads(self.pointer.read_text("utf-8"))["version"] if self.pointer.exists() else None

    def install(self, archive: Path, version: str) -> Path:
        target = self.releases / version
        if target.exists():
            return target
        temporary = Path(tempfile.mkdtemp(prefix=".release-", dir=self.releases))
        try:
            if zipfile.is_zipfile(archive):
                with zipfile.ZipFile(archive) as value:
                    for info in value.infolist():
                        destination = (temporary / info.filename).resolve()
                        if temporary.resolve() not in destination.parents and destination != temporary.resolve():
                            raise ValueError("Unsafe release archive")
                    value.extractall(temporary)
            elif tarfile.is_tarfile(archive):
                with tarfile.open(archive) as value:
                    value.extractall(temporary, filter="data")
            else:
                raise ValueError("Unsupported Edge release archive")
            os.replace(temporary, target)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        return target

    def switch(self, version: str) -> str | None:
        target = self.releases / version
        if not target.is_dir():
            raise ValueError("Release is not installed")
        current_link = self.root / "current"
        previous = self.current()
        current_is_link = current_link.is_symlink() or (
            hasattr(current_link, "is_junction") and current_link.is_junction()
        )
        if previous is None and current_is_link:
            previous = current_link.resolve().name
        temporary_link = self.root / f".current-{os.getpid()}"
        if temporary_link.exists() or temporary_link.is_symlink():
            temporary_link.unlink()
        os.symlink(target, temporary_link, target_is_directory=True)
        if current_link.exists() and not current_is_link:
            raise ValueError("Edge current pointer is not a managed symlink")
        if os.name == "nt" and current_is_link:
            current_link.unlink()
        os.replace(temporary_link, current_link)
        temporary = self.pointer.with_suffix(".tmp")
        temporary.write_text(json.dumps({"version": version, "previous": previous}), "utf-8")
        os.replace(temporary, self.pointer)
        return previous

    def rollback(self) -> str:
        value = json.loads(self.pointer.read_text("utf-8"))
        previous = value.get("previous")
        if not previous:
            raise ValueError("No previous Edge release is available")
        self.switch(previous)
        return previous
