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
            reference="funpay.cardinal@1.3.9",
            filename="funpay.cardinal-1.3.9.buywell-edge.zip",
            download_url="https://github.com/moreveal/buywell-runtimes/releases/download/funpay.cardinal-v1.3.9/funpay.cardinal-1.3.9.buywell-edge.zip",
            archive_sha256="8b47954f94180d6012c391289309215546696c2b41a910c70dabaf28bdf0fab7",
            public_key=_PUBLIC_KEY,
        ),
        OfficialPackage(
            reference="ggsel.seller@1.2.6",
            filename="ggsel.seller-1.2.6.buywell-edge.zip",
            download_url="https://github.com/moreveal/buywell-runtimes/releases/download/ggsel.seller-v1.2.6/ggsel.seller-1.2.6.buywell-edge.zip",
            archive_sha256="1adf80dcff33c69b19f4513d324e852e9f7502afd7457b287e987b45099b4e42",
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
