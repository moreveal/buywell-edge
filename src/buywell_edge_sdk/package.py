from __future__ import annotations

import base64
import hashlib
import importlib
import json
import os
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field

from .contracts import ExtensionDefinition

FIXED_TIMESTAMP = (2020, 1, 1, 0, 0, 0)
MAX_FILES = 500
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_EXPANDED_BYTES = 64 * 1024 * 1024


class SigningInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    algorithm: str = "ed25519"
    public_key: str = Field(alias="publicKey")
    signature: str


@dataclass(frozen=True)
class PackageInspection:
    manifest: dict[str, Any]
    digest: str
    signed: bool
    files: tuple[str, ...]


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def load_extension(reference: str) -> ExtensionDefinition:
    module_name, separator, object_name = reference.partition(":")
    if not separator:
        raise ValueError("Extension reference must use module:object syntax")
    value = getattr(importlib.import_module(module_name), object_name)
    if not isinstance(value, ExtensionDefinition):
        raise TypeError("Entrypoint object is not an ExtensionDefinition")
    return value


def _safe_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or not path.parts:
        raise ValueError(f"Unsafe package path: {name}")
    return path


def _entry_digest(manifest: dict[str, Any], entries: list[tuple[str, bytes]]) -> str:
    unsigned = json.loads(json.dumps(manifest))
    unsigned.pop("signing", None)
    unsigned.pop("package", None)
    digest = hashlib.sha256()
    digest.update(canonical_json(unsigned))
    for name, content in sorted(entries):
        digest.update(name.encode("utf-8"))
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()


def generate_signing_key(path: Path) -> Ed25519PrivateKey:
    key = Ed25519PrivateKey.generate()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


def load_signing_key(path: Path) -> Ed25519PrivateKey:
    value = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(value, Ed25519PrivateKey):
        raise ValueError("Signing key must be Ed25519")
    return value


def build_package(
    extension: ExtensionDefinition,
    source_directory: Path,
    output: Path,
    *,
    signing_key: Ed25519PrivateKey | None = None,
    include: tuple[str, ...] = ("*.py", "**/*.py", "README*.md", "CHANGELOG*.md", "guides/*.md", "assets/*"),
) -> PackageInspection:
    root = source_directory.resolve()
    selected: dict[str, bytes] = {}
    for pattern in include:
        for source in root.glob(pattern):
            if not source.is_file() or "__pycache__" in source.parts:
                continue
            relative = source.relative_to(root).as_posix()
            _safe_path(relative)
            content = source.read_bytes()
            if len(content) > MAX_FILE_BYTES:
                raise ValueError(f"Package file is too large: {relative}")
            selected[relative] = content
    entries = sorted(selected.items())
    if not entries:
        raise ValueError("Package source directory is empty")
    if len(entries) > MAX_FILES or sum(len(value) for _, value in entries) > MAX_EXPANDED_BYTES:
        raise ValueError("Package exceeds safety limits")

    manifest = extension.manifest()
    documentation = manifest.get("documentation", {})
    referenced_documentation = {
        value
        for localized in documentation.values()
        for value in localized.values()
    }
    missing_documentation = sorted(referenced_documentation - set(selected))
    if missing_documentation:
        raise FileNotFoundError(
            "Package documentation is missing from the selected source: "
            + ", ".join(missing_documentation)
        )
    digest = _entry_digest(manifest, entries)
    manifest["package"] = {
        "artifact": "extension/",
        "digest": digest,
        "files": [{"path": name, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()} for name, content in entries],
    }
    if signing_key:
        public = signing_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        manifest["signing"] = SigningInfo(
            publicKey=base64.b64encode(public).decode(),
            signature=base64.b64encode(signing_key.sign(bytes.fromhex(digest))).decode(),
        ).model_dump(by_alias=True)

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, content in [("manifest.json", canonical_json(manifest)), *[(f"extension/{name}", content) for name, content in entries]]:
            info = zipfile.ZipInfo(name, FIXED_TIMESTAMP)
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    return PackageInspection(manifest=manifest, digest=digest, signed=signing_key is not None, files=tuple(name for name, _ in entries))


def inspect_package(path: Path) -> PackageInspection:
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_FILES + 1 or sum(info.file_size for info in infos) > MAX_EXPANDED_BYTES:
            raise ValueError("Package exceeds safety limits")
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise ValueError("Package contains duplicate files")
        for name in names:
            _safe_path(name)
        manifest = json.loads(archive.read("manifest.json"))
        inventory = manifest.get("package", {}).get("files", [])
        inventory_names = [str(item["path"]) for item in inventory]
        if len(inventory_names) != len(set(inventory_names)):
            raise ValueError("Package manifest contains duplicate files")
        for name in inventory_names:
            _safe_path(name)
        expected_names = {"manifest.json", *(f"extension/{name}" for name in inventory_names)}
        if set(names) != expected_names:
            raise ValueError("Package archive does not match its signed file inventory")
        entries = []
        for item in inventory:
            name = str(item["path"])
            content = archive.read(f"extension/{name}")
            if len(content) != item["size"] or hashlib.sha256(content).hexdigest() != item["sha256"]:
                raise ValueError(f"Package file verification failed: {name}")
            entries.append((name, content))
    digest = _entry_digest(manifest, entries)
    if manifest.get("package", {}).get("digest") != digest:
        raise ValueError("Package digest does not match")
    return PackageInspection(manifest=manifest, digest=digest, signed="signing" in manifest, files=tuple(name for name, _ in entries))


def verify_package(path: Path, trusted_keys: set[bytes] | None = None, *, allow_unsigned: bool = False) -> PackageInspection:
    inspected = inspect_package(path)
    signing = inspected.manifest.get("signing")
    if not signing:
        if allow_unsigned:
            return inspected
        raise ValueError("Unsigned packages are allowed only in developer mode")
    public = base64.b64decode(signing["publicKey"], validate=True)
    signature = base64.b64decode(signing["signature"], validate=True)
    if trusted_keys is not None and public not in trusted_keys:
        raise ValueError("Package signer is not trusted")
    Ed25519PublicKey.from_public_bytes(public).verify(signature, bytes.fromhex(inspected.digest))
    return inspected
