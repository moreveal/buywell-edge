from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import jsonschema
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from buywell_edge_sdk.package import build_package, verify_package
from buywell_edge_sdk.contracts import module


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples"))


def test_manifest_is_generated_from_typed_contracts():
    from reference_module import extension

    manifest = extension.manifest()
    assert manifest["schemaVersion"] == 2
    assert manifest["extension"]["kind"] == "module"
    assert manifest["contracts"]["actions"][0]["id"] == "send-message"
    assert manifest["contracts"]["actions"][0]["input_schema"]["properties"]["text"]["type"] == "string"
    assert manifest["configuration"]["schema"]["properties"]["prefix"]["x-buywell-label"] == {
        "ru": "Префикс сообщения",
        "en": "Message prefix",
    }


def test_adapter_secrets_are_discovered():
    from reference_adapter import extension

    manifest = extension.manifest()
    assert manifest["executionPolicy"] == "edge_required"
    assert manifest["configuration"]["secretFields"] == ["api_key"]


def test_package_is_deterministic_and_signed(tmp_path: Path):
    from reference_module import extension

    key = Ed25519PrivateKey.generate()
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    one = build_package(extension, ROOT / "examples", first, signing_key=key)
    two = build_package(extension, ROOT / "examples", second, signing_key=key)
    assert one.digest == two.digest
    assert first.read_bytes() == second.read_bytes()
    assert verify_package(first).digest == one.digest


def test_package_rejects_files_outside_signed_inventory(tmp_path: Path):
    from reference_module import extension

    archive = tmp_path / "extension.zip"
    build_package(
        extension,
        ROOT / "examples",
        archive,
        signing_key=Ed25519PrivateKey.generate(),
    )
    with zipfile.ZipFile(archive, "a") as package:
        package.writestr("extension/unsigned.py", "raise RuntimeError('unsigned')")

    with pytest.raises(
        ValueError,
        match="does not match its signed file inventory",
    ):
        verify_package(archive)


def test_legacy_module_contract_is_preserved_without_rewriting(tmp_path: Path):
    legacy = {
        "schemaVersion": 1,
        "protocolVersion": "1.0.0",
        "module": {"id": "legacy.market", "version": "1.4.2"},
        "events": [{"type": "commerce.purchase.created", "version": "1.1.0"}],
        "nodes": [{"type": "legacy.market/reply", "version": "1.0.0"}],
        "bindingCatalogs": [{"id": "legacy.catalog", "version": "1.0.0"}],
    }
    definition = module(
        extension_id="legacy.market",
        version="1.4.2",
        display_name={"ru": "Legacy", "en": "Legacy"},
        publisher="Buywell",
        entrypoint="legacy:extension",
        legacy_manifest=legacy,
    )
    generated = definition.manifest()["compatibility"]["moduleManifest"]
    assert generated == legacy
    assert generated["events"] == legacy["events"]
    jsonschema.validate(
        definition.manifest(),
        json.loads((ROOT / "protocol" / "manifest-v2.schema.json").read_text("utf-8")),
    )


def test_full_commit_source_dependency_is_accepted():
    definition = module(
        extension_id="example.git",
        version="1.0.0",
        display_name={"ru": "Git", "en": "Git"},
        publisher="Example",
        entrypoint="example:extension",
        dependencies=[
            "playerokapi @ https://github.com/alleexxeeyy/PlayerokAPI/archive/"
            "e2084a382081a584d24abb96cc1a64e5cb79a860.zip"
        ],
    )
    assert definition.manifest()["runtime"]["dependencies"][0].endswith(
        "e2084a382081a584d24abb96cc1a64e5cb79a860.zip"
    )
