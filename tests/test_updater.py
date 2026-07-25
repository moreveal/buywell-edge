from __future__ import annotations

import io
import os
import tarfile
from pathlib import Path

from buywell_edge.updater import ReleaseManager


def release_archive(path: Path) -> Path:
    with tarfile.open(path, "w:gz") as archive:
        payload = b"edge"
        info = tarfile.TarInfo("bin/buywell-edge")
        info.mode = 0o755
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    return path


def test_install_makes_release_root_traversable(tmp_path: Path) -> None:
    manager = ReleaseManager(tmp_path / "edge")

    release = manager.install(release_archive(tmp_path / "edge.tar.gz"), "1.2.3")

    if os.name != "nt":
        assert release.stat().st_mode & 0o055 == 0o055


def test_install_repairs_existing_release_permissions(tmp_path: Path) -> None:
    manager = ReleaseManager(tmp_path / "edge")
    release = manager.releases / "1.2.3"
    release.mkdir()
    if os.name != "nt":
        release.chmod(0o700)

    assert manager.install(tmp_path / "unused.tar.gz", "1.2.3") == release

    if os.name != "nt":
        assert release.stat().st_mode & 0o055 == 0o055
