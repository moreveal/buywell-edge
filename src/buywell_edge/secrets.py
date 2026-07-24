from __future__ import annotations

import base64
import json
import os
import ctypes
from ctypes import wintypes
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(value: bytes, *, protect: bool) -> bytes:
    buffer = ctypes.create_string_buffer(value)
    source = _DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if protect:
        success = crypt32.CryptProtectData(ctypes.byref(source), "Buywell Edge", None, None, None, 0, ctypes.byref(output))
    else:
        success = crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(output))
    if not success:
        raise OSError("Windows DPAPI operation failed")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)


class SecretVault:
    """Machine-local encrypted vault.

    Windows protects the master key with DPAPI when pywin32 is available.
    Headless Linux stores a random key in a mode-0600 file owned by the Edge
    service account. Values are never placed in process arguments or env.
    """

    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.directory = directory
        self.key_file = directory / "vault.key"
        self.values_file = directory / "vault.json"
        self._key = self._load_or_create_key()

    def _load_or_create_key(self) -> bytes:
        if self.key_file.exists():
            wrapped = self.key_file.read_bytes()
            if os.name == "nt":
                return _dpapi(wrapped, protect=False)
            return wrapped
        key = AESGCM.generate_key(bit_length=256)
        wrapped = key
        if os.name == "nt":
            wrapped = _dpapi(key, protect=True)
        self.key_file.write_bytes(wrapped)
        try:
            os.chmod(self.key_file, 0o600)
        except OSError:
            pass
        return key

    def _read(self) -> dict[str, str]:
        return json.loads(self.values_file.read_text("utf-8")) if self.values_file.exists() else {}

    def put(self, reference: str, value: dict[str, str]) -> None:
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._key).encrypt(nonce, json.dumps(value, ensure_ascii=False).encode(), reference.encode())
        values = self._read()
        values[reference] = base64.b64encode(nonce + ciphertext).decode()
        self.values_file.write_text(json.dumps(values, sort_keys=True), "utf-8")
        try:
            os.chmod(self.values_file, 0o600)
        except OSError:
            pass

    def get(self, reference: str | None) -> dict[str, str]:
        if not reference:
            return {}
        encoded = self._read().get(reference)
        if not encoded:
            return {}
        value = base64.b64decode(encoded)
        plaintext = AESGCM(self._key).decrypt(value[:12], value[12:], reference.encode())
        return {key: str(item) for key, item in json.loads(plaintext).items()}

    def delete(self, reference: str) -> None:
        values = self._read()
        values.pop(reference, None)
        self.values_file.write_text(json.dumps(values, sort_keys=True), "utf-8")
