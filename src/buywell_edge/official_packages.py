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
            reference="funpay.cardinal@1.3.0",
            filename="funpay.cardinal-1.3.0.buywell-edge.zip",
            archive_sha256="17314616409d3af7d95942db7602158236eb3d73ded85018078a9ae45da38b43",
            public_key=_PUBLIC_KEY,
        ),
        OfficialPackage(
            reference="ggsel.seller@1.2.3",
            filename="ggsel.seller-1.2.3.buywell-edge.zip",
            archive_sha256="49a0071a1a18ebebc63229f34c29e0f4cafad4e6858a429d8758c6ea199f2b9e",
            public_key=_PUBLIC_KEY,
        ),
        OfficialPackage(
            reference="playerok.universal@1.0.4",
            filename="playerok.universal-1.0.4.buywell-edge.zip",
            archive_sha256="1c72520873636050450b89919927914c6319a574333bfe6ce7fecda806611e64",
            public_key=_PUBLIC_KEY,
        ),
        OfficialPackage(
            reference="adapter.ns-gifts@1.0.0",
            filename="adapter.ns-gifts-1.0.0.buywell-edge.zip",
            archive_sha256="68cf518827cae712ec05fa4d843ac9bcfff8922ade494ab2d84b9406148b025c",
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
