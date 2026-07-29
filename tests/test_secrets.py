from __future__ import annotations

from buywell_edge import secrets


def test_windows_migrates_legacy_user_dpapi_key_to_machine_scope(
    monkeypatch, tmp_path
) -> None:
    key = b"k" * 32
    key_file = tmp_path / "vault.key"
    key_file.write_bytes(b"legacy-user-wrapped-key")
    calls: list[tuple[bytes, bool, bool]] = []
    monkeypatch.setattr(secrets.os, "name", "nt")

    def dpapi(value: bytes, *, protect: bool, machine_scope: bool = False) -> bytes:
        calls.append((value, protect, machine_scope))
        return b"machine-wrapped-key" if protect else key

    monkeypatch.setattr(secrets, "_dpapi", dpapi)

    vault = secrets.SecretVault(tmp_path)

    assert vault._key == key
    assert key_file.read_bytes() == (
        secrets._MACHINE_SCOPE_PREFIX + b"machine-wrapped-key"
    )
    assert calls == [
        (b"legacy-user-wrapped-key", False, False),
        (key, True, True),
    ]


def test_windows_new_vault_uses_machine_scope(monkeypatch, tmp_path) -> None:
    calls: list[tuple[bytes, bool, bool]] = []
    monkeypatch.setattr(secrets.os, "name", "nt")

    def dpapi(value: bytes, *, protect: bool, machine_scope: bool = False) -> bytes:
        calls.append((value, protect, machine_scope))
        return b"machine-wrapped-key"

    monkeypatch.setattr(secrets, "_dpapi", dpapi)

    secrets.SecretVault(tmp_path)

    assert (tmp_path / "vault.key").read_bytes() == (
        secrets._MACHINE_SCOPE_PREFIX + b"machine-wrapped-key"
    )
    assert calls[0][1:] == (True, True)
