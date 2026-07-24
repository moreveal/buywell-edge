# Buywell Edge SDK

Official and private extensions use the same `buywell-edge-sdk` package. A
module publishes marketplace events and actions; an `adapter-driver` executes
methods of a concrete API. The SDK owns framed stdio. An extension does not
implement WebSockets, a Buywell key, heartbeats, leases, an outbox, or
redelivery.

```python
from pydantic import BaseModel, SecretStr
from buywell_edge_sdk import adapter_driver

class Settings(BaseModel):
    api_key: SecretStr

class Request(BaseModel):
    item_id: str

driver = adapter_driver(
    extension_id="example.supplier",
    version="1.0.0",
    display_name={"ru": "Поставщик", "en": "Supplier"},
    publisher="Example",
    entrypoint="driver:driver",
    config_model=Settings,
    network_domains=["api.example.com"],
)

@driver.operation("example.supplier/reserve", "1.0.0", input_model=Request)
async def reserve(context, value):
    return {"reserved": True}
```

`SecretStr` and fields with `secret` JSON Schema metadata automatically become
`configuration.secretFields`. Pydantic models become JSON Schema. Dependencies
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

This does not alter Buywell v1 user authentication either: an existing
connection key keeps working for the immutable legacy runtime until an explicit
migration. Edge imports a provider session locally with user consent and gets
its own device credential through one-time pairing. Provider secrets and the
old Buywell key are never moved through the manifest or uploaded by the
control plane.

Provider implementation can evolve independently, including reviewed changes
from a pinned upstream source. Any public contract change still requires a new
module version and the usual contract tests.
