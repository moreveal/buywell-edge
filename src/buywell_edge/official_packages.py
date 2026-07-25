from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class OfficialPackage:
    reference: str
    filename: str
    archive_sha256: str
    public_key: bytes


_PUBLIC_KEY = base64.b64decode("HVntIeTZL5zbW8HN1XA3iMkD+J49J6slpCn7Pxpg/TQ=")

OFFICIAL_PACKAGES = {
    item.reference: item
    for item in (
        OfficialPackage(
            reference="funpay.cardinal@1.3.1",
            filename="funpay.cardinal-1.3.1.buywell-edge.zip",
            archive_sha256="3c0002cf5493a0c3bb3eda944503e421ab997954c1dee90d12708ac3f0216597",
            public_key=_PUBLIC_KEY,
        ),
        OfficialPackage(
            reference="ggsel.seller@1.2.4",
            filename="ggsel.seller-1.2.4.buywell-edge.zip",
            archive_sha256="30adf63bbe04848aa0372043a538d736abc94975a16c10e80748778cdccbad2c",
            public_key=_PUBLIC_KEY,
        ),
        OfficialPackage(
            reference="playerok.universal@1.0.5",
            filename="playerok.universal-1.0.5.buywell-edge.zip",
            archive_sha256="0d46de25a0c452c6b45ee2c10680e0ca8f3b4df433b91ab7de0887069650c7e8",
            public_key=_PUBLIC_KEY,
        ),
        OfficialPackage(
            reference="adapter.ns-gifts@1.0.5",
            filename="adapter.ns-gifts-1.0.5.buywell-edge.zip",
            archive_sha256="50320f8bbd90701eb61201f51c88baff39cc72149a9965acbe55647ccd3e074e",
            public_key=_PUBLIC_KEY,
        ),
    )
}


def official_package(reference: str) -> OfficialPackage | None:
    return OFFICIAL_PACKAGES.get(reference.strip().lower())


def verify_archive(package: OfficialPackage, content: bytes) -> None:
    actual = hashlib.sha256(content).hexdigest()
    if actual != package.archive_sha256:
        raise ValueError("Official package download checksum does not match")
