from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class OfficialPackage:
    reference: str
    filename: str
    download_url: str
    archive_sha256: str
    public_key: bytes


_PUBLIC_KEY = base64.b64decode("HVntIeTZL5zbW8HN1XA3iMkD+J49J6slpCn7Pxpg/TQ=")

OFFICIAL_PACKAGES = {
    item.reference: item
    for item in (
        OfficialPackage(
            reference="funpay.cardinal@1.3.4",
            filename="funpay.cardinal-1.3.4.buywell-edge.zip",
            download_url="https://github.com/moreveal/buywell-runtimes/releases/download/funpay.cardinal-v1.3.4/funpay.cardinal-1.3.4.buywell-edge.zip",
            archive_sha256="40346ccb084082a5a8a6dde7dfa342405b2316a45829e4b0ddcba6ed9f7bb7e9",
            public_key=_PUBLIC_KEY,
        ),
        OfficialPackage(
            reference="ggsel.seller@1.2.4",
            filename="ggsel.seller-1.2.4.buywell-edge.zip",
            download_url="https://github.com/moreveal/buywell-runtimes/releases/download/ggsel.seller-v1.2.4/ggsel.seller-1.2.4.buywell-edge.zip",
            archive_sha256="30adf63bbe04848aa0372043a538d736abc94975a16c10e80748778cdccbad2c",
            public_key=_PUBLIC_KEY,
        ),
        OfficialPackage(
            reference="playerok.universal@1.0.5",
            filename="playerok.universal-1.0.5.buywell-edge.zip",
            download_url="https://github.com/moreveal/buywell-runtimes/releases/download/playerok.universal-v1.0.5/playerok.universal-1.0.5.buywell-edge.zip",
            archive_sha256="0d46de25a0c452c6b45ee2c10680e0ca8f3b4df433b91ab7de0887069650c7e8",
            public_key=_PUBLIC_KEY,
        ),
        OfficialPackage(
            reference="adapter.ns-gifts@1.0.6",
            filename="adapter.ns-gifts-1.0.6.buywell-edge.zip",
            download_url="https://github.com/moreveal/buywell-runtimes/releases/download/adapter.ns-gifts-v1.0.6/adapter.ns-gifts-1.0.6.buywell-edge.zip",
            archive_sha256="064f46ca0accf2976eb84ca7ecc0ba07820a1b939bc179cf914b04b9a184ac45",
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
