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
            reference="funpay.cardinal@1.3.5",
            filename="funpay.cardinal-1.3.5.buywell-edge.zip",
            download_url="https://github.com/moreveal/buywell-runtimes/releases/download/funpay.cardinal-v1.3.5/funpay.cardinal-1.3.5.buywell-edge.zip",
            archive_sha256="871d7242ce3c98e5ca1138851d25d996d706939fe7540fc4fc14a51381db1663",
            public_key=_PUBLIC_KEY,
        ),
        OfficialPackage(
            reference="ggsel.seller@1.2.5",
            filename="ggsel.seller-1.2.5.buywell-edge.zip",
            download_url="https://github.com/moreveal/buywell-runtimes/releases/download/ggsel.seller-v1.2.5/ggsel.seller-1.2.5.buywell-edge.zip",
            archive_sha256="7d832edd495cbab93c382f5d01c6733c82e3e8a283310e9502ae1106e69df728",
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
