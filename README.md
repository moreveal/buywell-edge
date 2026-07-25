# Buywell Edge

Buywell Edge is the user-owned execution environment for Buywell marketplace
modules and adapter drivers. The daemon keeps provider credentials on the
user's machine, multiplexes one outbound connection to Buywell, and owns
durable delivery, health, package versions, and rollback.

The repository contains both the daemon and the public Python SDK. Official
extensions use exactly the same decorators, package builder, and stdio
protocol as private extensions.

[SDK на русском](docs/sdk.ru.md) · [SDK in English](docs/sdk.en.md)

## Install

Linux:

```bash
curl -fsSL https://buywell.pro/edge/install.sh | sudo sh -s -- PAIR-CODE
```

Linux releases support x86-64 and ARM64 distributions with glibc 2.28 or
newer, including Debian 10+, Ubuntu 20.04+, and their current derivatives.
The installer checks this requirement before changing the service.

Windows PowerShell as Administrator:

```powershell
irm https://buywell.pro/edge/install.ps1 | iex
buywell-edge connect PAIR-CODE
```

Release archives contain CPython 3.12 in a self-contained, pre-extracted
runtime. They do not depend on a system Python. Unlike a single-file freezer,
the installed runtime does not unpack Python again on every CLI or extension
process start. Linux installs a hardened systemd service and stable CLI link;
Windows installs an automatic Windows Service and adds the stable release
junction to the machine PATH.

The executable also hosts the private Python launcher used by extension
processes and locked dependency installation. Extensions never call a system
Python, even during install, self-test, update, or rollback.

Update Edge in place without pairing the server again or duplicating its
connections:

```bash
sudo buywell-edge update
```

The command verifies the latest release, switches the existing installation,
restarts the service without blocking on systemd, and preserves the current
and previous release for rollback.

## Connections and modules

Common operations use connection names and module versions; internal IDs and
package digests are only exposed by the optional JSON output:

```bash
buywell-edge connection list
buywell-edge module list
buywell-edge module install adapter.ns-gifts@1.0.6
buywell-edge module update insignetop
buywell-edge module switch insignetop 1.0.6
buywell-edge connection remove insignetop
```

`module update` downloads the newest official package, verifies it, performs
an atomic connection cutover, and removes the previous package after the new
process becomes ready. A manual `module switch` retains the last confirmed
health while the old process drains instead of publishing a false offline
transition.

Use `connection list --json` or `module list --json` for automation that
needs exact identifiers.

## Development

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
pytest
buywell-edge --help
```

## Extension authoring

```python
from pydantic import BaseModel
from buywell_edge_sdk import module

class Message(BaseModel):
    text: str

extension = module(
    extension_id="example.market",
    version="1.0.0",
    display_name={"ru": "Пример", "en": "Example"},
    publisher="Example",
    entrypoint="example:extension",
)

@extension.action("send-message", "1.0.0", input_model=Message)
async def send_message(context, value: Message):
    return {"delivered": True}
```

Builds never execute on Buywell servers. `buywell-edge module build` imports
the declaration locally, validates it, and writes a deterministic signed
package. The Buywell control plane only reads the generated manifest. A signed
adapter driver supplies its operation schemas through the Edge connection;
after signature verification, Buywell exposes those blocks only to the
connected account.
