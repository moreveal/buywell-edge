from __future__ import annotations

import json
import hashlib
import os
import platform
import shutil
import subprocess
import tempfile
import tarfile
import zipfile
from pathlib import Path

import httpx


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
            self._make_release_traversable(target)
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
            self._make_release_traversable(temporary)
            os.replace(temporary, target)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        return target

    @staticmethod
    def _make_release_traversable(release: Path) -> None:
        """Allow the dedicated service account to execute a root-installed release."""
        if os.name == "nt":
            subprocess.run(
                ["icacls.exe", str(release), "/reset", "/T", "/C", "/Q"],
                check=True,
            )
            return
        release.chmod(release.stat().st_mode | 0o055)

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

    def download(
        self,
        version: str = "latest",
        repository: str = "moreveal/buywell-edge",
    ) -> tuple[str, Path]:
        if os.name == "nt":
            asset = "buywell-edge-windows-x86_64.zip"
        else:
            architecture = platform.machine().lower()
            suffix = "aarch64" if architecture in {"aarch64", "arm64"} else "x86_64"
            asset = f"buywell-edge-linux-{suffix}.tar.gz"
        endpoint = (
            f"https://api.github.com/repos/{repository}/releases/latest"
            if version == "latest"
            else f"https://api.github.com/repos/{repository}/releases/tags/v{version.lstrip('v')}"
        )
        response = httpx.get(endpoint, follow_redirects=True, timeout=60)
        response.raise_for_status()
        release = response.json()
        resolved_version = str(release["tag_name"]).removeprefix("v")
        assets = {
            str(item["name"]): str(item["browser_download_url"])
            for item in release.get("assets", [])
        }
        if asset not in assets or f"{asset}.sha256" not in assets:
            raise RuntimeError(f"Release {resolved_version} does not contain {asset}")
        archive_response = httpx.get(assets[asset], follow_redirects=True, timeout=120)
        archive_response.raise_for_status()
        checksum_response = httpx.get(
            assets[f"{asset}.sha256"],
            follow_redirects=True,
            timeout=60,
        )
        checksum_response.raise_for_status()
        expected = checksum_response.text.split()[0].lower()
        actual = hashlib.sha256(archive_response.content).hexdigest()
        if expected != actual:
            raise RuntimeError("Buywell Edge release checksum verification failed")
        suffix = ".zip" if asset.endswith(".zip") else ".tar.gz"
        handle, filename = tempfile.mkstemp(suffix=suffix)
        os.close(handle)
        archive = Path(filename)
        archive.write_bytes(archive_response.content)
        return resolved_version, archive

    def prune(self, keep: set[str]) -> None:
        releases = self.releases.resolve()
        for path in self.releases.iterdir():
            if path.name in keep:
                continue
            resolved = path.resolve()
            if releases not in resolved.parents:
                raise ValueError("Release directory is outside the managed root")
            if path.is_dir():
                shutil.rmtree(path)
