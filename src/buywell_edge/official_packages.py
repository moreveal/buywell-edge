from __future__ import annotations

import base64
import hashlib
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx


@dataclass(frozen=True)
class OfficialPackage:
    reference: str
    filename: str
    download_url: str
    archive_sha256: str
    public_key: bytes


_PUBLIC_KEY = base64.b64decode("HVntIeTZL5zbW8HN1XA3iMkD+J49J6slpCn7Pxpg/TQ=")
_IDENTIFIER = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)+$")
_SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def parse_official_package_catalog(
    payload: object,
    buywell_url: str,
) -> dict[str, OfficialPackage]:
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
        raise ValueError("Official package catalog schema is unsupported")
    raw_packages = payload.get("packages")
    if not isinstance(raw_packages, list):
        raise ValueError("Official package catalog is missing packages")
    packages: dict[str, OfficialPackage] = {}
    expected_keys = {
        "extensionId",
        "version",
        "filename",
        "downloadPath",
        "archiveSha256",
    }
    for raw in raw_packages:
        if not isinstance(raw, dict) or set(raw) != expected_keys:
            raise ValueError("Official package catalog entry is invalid")
        extension_id = raw["extensionId"]
        version = raw["version"]
        filename = raw["filename"]
        download_path = raw["downloadPath"]
        archive_sha256 = raw["archiveSha256"]
        if not isinstance(extension_id, str) or not _IDENTIFIER.fullmatch(extension_id):
            raise ValueError("Official package extension ID is invalid")
        if not isinstance(version, str) or not _SEMVER.fullmatch(version):
            raise ValueError("Official package version is invalid")
        expected_filename = f"{extension_id}-{version}.buywell-edge.zip"
        if filename != expected_filename:
            raise ValueError("Official package filename does not match its identity")
        if download_path != f"/edge/packages/{filename}":
            raise ValueError("Official package download path is invalid")
        if not isinstance(archive_sha256, str) or not _SHA256.fullmatch(archive_sha256):
            raise ValueError("Official package digest is invalid")
        reference = f"{extension_id}@{version}"
        if reference in packages:
            raise ValueError("Official package catalog contains a duplicate reference")
        packages[reference] = OfficialPackage(
            reference=reference,
            filename=filename,
            download_url=urljoin(f"{buywell_url.rstrip('/')}/", download_path.lstrip("/")),
            archive_sha256=archive_sha256,
            public_key=_PUBLIC_KEY,
        )
    return packages


def fetch_official_packages(
    buywell_url: str,
    request: Callable[..., httpx.Response] | None = None,
) -> dict[str, OfficialPackage]:
    response = (request or httpx.get)(
        f"{buywell_url.rstrip('/')}/api/v2/edge/official-packages",
        follow_redirects=True,
        timeout=30,
    )
    response.raise_for_status()
    return parse_official_package_catalog(response.json(), buywell_url)


def official_package(
    reference: str,
    packages: Mapping[str, OfficialPackage],
) -> OfficialPackage | None:
    return packages.get(reference.strip().lower())


def verify_archive(package: OfficialPackage, content: bytes) -> None:
    actual = hashlib.sha256(content).hexdigest()
    if actual != package.archive_sha256:
        raise ValueError("Official package download checksum does not match")
