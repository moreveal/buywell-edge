import os

import httpx
import pytest

from buywell_edge.config import default_state_directory
from buywell_edge.official_packages import (
    fetch_official_packages,
    official_package,
    parse_official_package_catalog,
    verify_archive,
)


CATALOG = {
    "schemaVersion": 1,
    "packages": [
        {
            "extensionId": "funpay.cardinal",
            "version": "1.3.10",
            "filename": "funpay.cardinal-1.3.10.buywell-edge.zip",
            "downloadPath": "/edge/packages/funpay.cardinal-1.3.10.buywell-edge.zip",
            "archiveSha256": "30a3f578f326a889c4790b5120769536658188e8b2a079cf38bcb0ff136fc81a",
        }
    ],
}


def test_resolves_official_reference_case_insensitively() -> None:
    packages = parse_official_package_catalog(CATALOG, "https://buywell.pro")
    package = official_package(" FunPay.Cardinal@1.3.10 ", packages)

    assert package is not None
    assert package.filename == "funpay.cardinal-1.3.10.buywell-edge.zip"
    assert package.download_url == "https://buywell.pro/edge/packages/funpay.cardinal-1.3.10.buywell-edge.zip"
    assert package.archive_sha256 == "30a3f578f326a889c4790b5120769536658188e8b2a079cf38bcb0ff136fc81a"
    assert len(package.public_key) == 32


def test_rejects_content_outside_pinned_release() -> None:
    packages = parse_official_package_catalog(CATALOG, "https://buywell.pro")
    package = official_package("funpay.cardinal@1.3.10", packages)
    assert package is not None

    with pytest.raises(ValueError, match="checksum"):
        verify_archive(package, b"not the official archive")


def test_rejects_catalog_paths_that_do_not_match_the_package_identity() -> None:
    invalid = {
        **CATALOG,
        "packages": [{**CATALOG["packages"][0], "downloadPath": "/other.zip"}],
    }
    with pytest.raises(ValueError, match="download path"):
        parse_official_package_catalog(invalid, "https://buywell.pro")


def test_fetches_the_catalog_from_the_paired_buywell_origin() -> None:
    requested: list[str] = []

    def request(url: str, **_kwargs) -> httpx.Response:
        requested.append(url)
        return httpx.Response(200, json=CATALOG, request=httpx.Request("GET", url))

    packages = fetch_official_packages("https://automation.example", request)

    assert requested == ["https://automation.example/api/v2/edge/official-packages"]
    assert packages["funpay.cardinal@1.3.10"].download_url == (
        "https://automation.example/edge/packages/funpay.cardinal-1.3.10.buywell-edge.zip"
    )


@pytest.mark.skipif(os.name == "nt", reason="Linux service account path")
def test_service_account_uses_service_state_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BUYWELL_EDGE_STATE_DIR", raising=False)
    monkeypatch.setattr("buywell_edge.config.os.geteuid", lambda: 123)
    monkeypatch.setattr("buywell_edge.config.getpass.getuser", lambda: "buywell-edge")

    assert default_state_directory().as_posix() == "/var/lib/buywell-edge"


def test_pairing_locale_is_preferred_over_host_locale(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from buywell_edge.cli import _locale
    from buywell_edge.config import EdgeConfig
    from buywell_edge.service import EdgeService

    service = EdgeService(EdgeConfig(
        state_directory=tmp_path / "state",
        install_directory=tmp_path / "install",
        buywell_url="https://buywell.pro",
    ))
    service.store.set_metadata("locale", "ru")
    monkeypatch.setenv("LANG", "en_US.UTF-8")

    assert _locale(service) == "ru"
