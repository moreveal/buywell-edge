import os

import pytest

from buywell_edge.config import default_state_directory
from buywell_edge.official_packages import official_package, verify_archive


def test_resolves_official_reference_case_insensitively() -> None:
    package = official_package(" FunPay.Cardinal@1.3.0 ")

    assert package is not None
    assert package.filename == "funpay.cardinal-1.3.0.buywell-edge.zip"
    assert len(package.public_key) == 32


def test_rejects_content_outside_pinned_release() -> None:
    package = official_package("funpay.cardinal@1.3.0")
    assert package is not None

    with pytest.raises(ValueError, match="checksum"):
        verify_archive(package, b"not the official archive")


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
