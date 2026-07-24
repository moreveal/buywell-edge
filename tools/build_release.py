from __future__ import annotations

import hashlib
import os
import platform
import shutil
import tarfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"


def platform_name() -> tuple[str, str]:
    system = platform.system().lower()
    machine = platform.machine().lower()
    architecture = "x86_64" if machine in {"amd64", "x86_64"} else "aarch64"
    return system, architecture


def write_checksum(archive: Path) -> None:
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(archive.suffix + ".sha256").write_text(
        f"{digest}  {archive.name}\n",
        encoding="ascii",
    )


def main() -> None:
    system, architecture = platform_name()
    bundle = DIST / "buywell-edge"
    executable = bundle / ("buywell-edge.exe" if system == "windows" else "buywell-edge")
    if not executable.is_file():
        raise SystemExit(f"PyInstaller onedir output was not found: {executable}")
    stage = DIST / "release"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir()
    if system == "windows":
        shutil.copytree(bundle, stage, dirs_exist_ok=True)
        archive = DIST / f"buywell-edge-windows-{architecture}.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
            for path in sorted(stage.rglob("*")):
                if path.is_file():
                    output.write(path, path.relative_to(stage).as_posix())
    else:
        shutil.copytree(bundle, stage / "bin")
        (stage / "share").mkdir()
        os.chmod(stage / "bin" / "buywell-edge", 0o755)
        shutil.copy2(ROOT / "install" / "buywell-edge.service", stage / "share" / "buywell-edge.service")
        archive = DIST / f"buywell-edge-linux-{architecture}.tar.gz"
        with tarfile.open(archive, "w:gz", format=tarfile.PAX_FORMAT) as output:
            output.add(stage / "bin", "bin")
            output.add(stage / "share", "share")
    write_checksum(archive)


if __name__ == "__main__":
    main()
