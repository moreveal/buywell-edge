from __future__ import annotations

import sys

from buywell_edge import __main__


def test_frozen_pip_registers_pyinstaller_loader(monkeypatch) -> None:
    import pip._vendor.distlib as distlib
    from pip._vendor.distlib import resources

    registered: list[tuple[object, object]] = []
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        resources,
        "register_finder",
        lambda loader, finder: registered.append((loader, finder)),
    )

    __main__._enable_frozen_pip_resources()

    assert registered == [(distlib.__loader__, resources.ResourceFinder)]


def test_unfrozen_pip_does_not_change_resource_finders(monkeypatch) -> None:
    from pip._vendor.distlib import resources

    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(
        resources,
        "register_finder",
        lambda *_: (_ for _ in ()).throw(AssertionError("unexpected registration")),
    )

    __main__._enable_frozen_pip_resources()
