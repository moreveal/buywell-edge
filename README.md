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
package. The Buywell control plane only reads the generated manifest.
