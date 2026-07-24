from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
import subprocess
import sys
import json
from pathlib import Path

from buywell_edge_sdk.package import PackageInspection, verify_package

from .storage import EdgeStore


class PackageManager:
    def __init__(self, root: Path, store: EdgeStore, *, developer_mode: bool = False) -> None:
        self.root = root
        self.store = store
        self.developer_mode = developer_mode
        self.root.mkdir(parents=True, exist_ok=True)

    def install(self, archive: Path, trusted_keys: set[bytes] | None = None) -> PackageInspection:
        effective_keys = None if self.developer_mode else (trusted_keys if trusted_keys is not None else set())
        inspected = verify_package(archive, effective_keys, allow_unsigned=self.developer_mode)
        extension = inspected.manifest["extension"]
        target = self.root / extension["id"] / extension["version"] / inspected.digest
        if target.exists():
            self.store.register_package(inspected.manifest, target)
            return inspected
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=".install-", dir=target.parent))
        try:
            with zipfile.ZipFile(archive) as package:
                package.extractall(temporary)
            (temporary / "state").mkdir()
            dependencies = inspected.manifest["runtime"].get("dependencies", [])
            if dependencies:
                target_dependencies = temporary / "dependencies"
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--no-deps", "--target", str(target_dependencies), *dependencies],
                    check=True,
                )
            source = temporary / "extension"
            dependency_directory = temporary / "dependencies"
            environment = os.environ.copy()
            python_paths = [str(source)]
            if dependency_directory.exists():
                python_paths.append(str(dependency_directory))
            if environment.get("PYTHONPATH"):
                python_paths.append(environment["PYTHONPATH"])
            environment["PYTHONPATH"] = os.pathsep.join(python_paths)
            subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import json;"
                        "from buywell_edge_sdk.package import load_extension;"
                        f"print(json.dumps(load_extension({json.dumps(inspected.manifest['runtime']['entrypoint'])}).manifest()))"
                    ),
                ],
                cwd=source,
                env=environment,
                check=True,
                timeout=30,
                stdout=subprocess.DEVNULL,
            )
            os.replace(temporary, target)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        self.store.register_package(inspected.manifest, target)
        return inspected

    def remove(self, extension_id: str, version: str, digest: str) -> None:
        package = self.store.package(extension_id, version, digest)
        if not package:
            raise ValueError("Package version is not installed")
        _, directory = package
        resolved = directory.resolve()
        if self.root.resolve() not in resolved.parents:
            raise ValueError("Package directory is outside the Edge package root")
        if self.store.package_in_use(extension_id, version, digest):
            raise ValueError("Package version is used by a connection")
        shutil.rmtree(resolved)
        self.store.remove_package(extension_id, version, digest)
