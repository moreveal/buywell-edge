# Buywell Edge SDK

Official and private extensions use the same `buywell-edge-sdk` package. A
module publishes marketplace events and actions; an `adapter-driver` executes
methods of a concrete API. The SDK owns framed stdio. An extension does not
implement WebSockets, a Buywell key, heartbeats, leases, an outbox, or
redelivery.

```python
from pydantic import BaseModel, SecretStr
from buywell_edge_sdk import adapter_driver, contract_field

class Settings(BaseModel):
    api_key: SecretStr

class Request(BaseModel):
    item_id: str = contract_field(
        label={"ru": "ID товара", "en": "Item ID"},
        description="Supplier item identifier.",
    )

class Result(BaseModel):
    reserved: bool = contract_field(
        label={"ru": "Зарезервировано", "en": "Reserved"},
    )

driver = adapter_driver(
    extension_id="example.supplier",
    version="1.0.0",
    display_name={"ru": "Поставщик", "en": "Supplier"},
    publisher="Example",
    entrypoint="driver:driver",
    config_model=Settings,
    network_domains=["api.example.com"],
)

@driver.operation(
    "example.supplier/reserve",
    "1.0.0",
    input_model=Request,
    output_model=Result,
    display_name={"ru": "Зарезервировать", "en": "Reserve"},
    description={"ru": "Зарезервировать товар.", "en": "Reserve an item."},
)
async def reserve(context, value):
    return Result(reserved=True)
```

The SDK adds `managedAdapter` metadata to the signed manifest. Once a
connection is created, Edge sends Buywell the complete manifest and its
`adapterOperations`. Buywell independently verifies the digest and Ed25519
signature, then creates account-scoped blocks and fields from the Pydantic
schemas. The same adapter no longer needs a separately maintained website
definition. A third-party package is not re-signed by Buywell or made visible
to other accounts.

When the adapter version differs from the driver version, pass
`adapter_version`, `adapter_dsl_namespace`, and
`adapter_definition_revision` to `adapter_driver(...)`.

`SecretStr` and fields with `secret` JSON Schema metadata automatically become
`configuration.secretFields`. Pydantic models become JSON Schema. Use
`contract_field(...)` for input and output fields: its RU/EN label and
description are written to the manifest and Buywell UI. An action handler must
return a mapping or Pydantic model matching `output_model`; the SDK validates
that before sending the result to Buywell. Dependencies
must use exact `name==version` pins. When a library is not on PyPI, an immutable
GitHub archive URL with a full 40-character commit SHA is allowed. `guides` and
`changelog` declare RU/EN Markdown files; the builder verifies that both files
are present in the package.

Build with:

```bash
buywell-edge module build driver:driver --source .
```

The first build creates a local Ed25519 developer key. Rebuilding identical
sources produces an identical archive and digest. Production Edge accepts
packages signed by trusted publishers; unsigned packages are limited to local
development mode.

## Migrating an existing module

Pass `legacy_manifest=Path("manifest.json")` to `module(...)`. The SDK embeds
the canonical v1 manifest in `preserve-v1` mode. Event, action, catalog,
resolver, and abstraction IDs and versions remain unchanged. The Edge package
has a separate version and digest, so published workflow revisions do not need
to be rewritten.

When Edge connects, it sends Buywell the complete signed package manifest.
This lets a private `preserve-v1` module be discovered like an official one:
its events, bindings, resolvers, and blocks become available only after the
signature, digest, and exact contract version have been verified.

This does not alter Buywell v1 user authentication either: an existing
connection key keeps working for the immutable legacy runtime until an explicit
migration. Edge imports a provider session locally with user consent and gets
its own device credential through one-time pairing. Provider secrets and the
old Buywell key are never moved through the manifest or uploaded by the
control plane.

Provider implementation can evolve independently, including reviewed changes
from a pinned upstream source. Any public contract change still requires a new
module version and the usual contract tests.
