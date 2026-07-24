from __future__ import annotations

import asyncio
import importlib
import json
import os
import platform
import subprocess
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Annotated

import typer
from pydantic import BaseModel
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from buywell_edge_sdk.contracts import ExtensionDefinition
from buywell_edge_sdk.package import build_package, generate_signing_key, load_signing_key
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from . import __version__
from .config import EdgeConfig
from .service import EdgeService, serve
from .storage import ConnectionRecord, EdgeStore
from .updater import ReleaseManager

app = typer.Typer(no_args_is_help=True, help="Buywell Edge")
module_app = typer.Typer(no_args_is_help=True)
connection_app = typer.Typer(no_args_is_help=True)
app.add_typer(module_app, name="module")
app.add_typer(connection_app, name="connection")
console = Console()


def _service() -> EdgeService:
    return EdgeService(EdgeConfig.load())


@app.command()
def version() -> None:
    console.print(f"Buywell Edge {__version__} · Python {platform.python_version()}")


@app.command()
def connect(
    code: Annotated[str, typer.Argument(help="One-time pairing code")],
    name: Annotated[str, typer.Option("--name")] = platform.node() or "Buywell Edge",
) -> None:
    service = _service()
    device_id, _ = asyncio.run(service.gateway.pair(code, name))
    console.print(f"[green]Connected.[/] Device {device_id[:8]} is now visible in Buywell.")


@app.command()
def run() -> None:
    asyncio.run(serve())


@app.command()
def status() -> None:
    service = _service()
    device = service.store.metadata("device_id")
    connections = service.store.connections()
    table = Table(title="Buywell Edge")
    table.add_column("Connection")
    table.add_column("Extension")
    table.add_column("Version")
    table.add_column("Status")
    for item in connections:
        color = "green" if item.health_state == "healthy" else "yellow" if item.health_state in ("degraded", "auth_required") else "dim"
        table.add_row(item.display_name, item.extension_id, item.extension_version, f"[{color}]{item.health_state}[/]")
    console.print(Panel(f"Device: {device[:8] if device else 'not connected'}\nVersion: {__version__}", title="Overview"))
    console.print(table)


@app.command()
def doctor() -> None:
    config = EdgeConfig.load()
    checks = {
        "State directory": config.state_directory.exists(),
        "Database": (config.state_directory / "edge.sqlite3").exists(),
        "Paired": bool(_service().store.metadata("device_id")),
        "Python 3.12": platform.python_version_tuple()[:2] == ("3", "12"),
    }
    for name, passed in checks.items():
        console.print(f"{'[green]✓[/]' if passed else '[red]✗[/]'} {name}")
    if not all(checks.values()):
        raise typer.Exit(1)


@app.command()
def tui() -> None:
    service = _service()
    sections = {
        "1": "Overview",
        "2": "Connections",
        "3": "Modules",
        "4": "Updates",
        "5": "Logs",
    }
    selected = "1"
    while True:
        console.clear()
        tabs = "  ".join(
            f"[bold cyan]{number} {name}[/]" if number == selected else f"[dim]{number} {name}[/]"
            for number, name in sections.items()
        )
        console.print(Panel(tabs, title=f"Buywell Edge {__version__}", border_style="cyan"))
        if selected == "1":
            device = service.store.metadata("device_id")
            connections = service.store.connections()
            healthy = sum(item.health_state == "healthy" for item in connections)
            console.print(
                Panel(
                    "\n".join(
                        [
                            f"Device          {device[:8] if device else '[yellow]not paired[/]'}",
                            f"Platform        {service.config.platform_name}",
                            f"Connections     {healthy}/{len(connections)} healthy",
                            f"Packages        {len(service.store.installed_packages())} installed",
                            f"Gateway         {service.config.buywell_url}",
                        ]
                    ),
                    title="Overview",
                )
            )
        elif selected == "2":
            table = Table(title="Connections", expand=True)
            for heading in ("Name", "Provider", "Version", "Enabled", "Health", "Last activity"):
                table.add_column(heading)
            for item in service.store.connections():
                color = "green" if item.health_state == "healthy" else "yellow" if item.health_state in ("degraded", "auth_required") else "dim"
                table.add_row(
                    item.display_name,
                    item.extension_id,
                    item.extension_version,
                    "yes" if item.enabled else "no",
                    f"[{color}]{item.health_state}[/]",
                    item.last_success_at or "—",
                )
            console.print(table)
        elif selected == "3":
            table = Table(title="Installed modules and drivers", expand=True)
            for heading in ("Extension", "Kind", "Version", "Digest", "Changelog"):
                table.add_column(heading)
            locale = "ru" if os.environ.get("LANG", "").lower().startswith("ru") else "en"
            for manifest, directory in service.store.installed_packages():
                extension = manifest["extension"]
                changelog_path = (
                    manifest.get("documentation", {})
                    .get("changelog", {})
                    .get(locale)
                )
                changelog = "—"
                if changelog_path:
                    path = directory / "extension" / changelog_path
                    if path.is_file():
                        changelog = next(
                            (
                                line.lstrip("# ").strip()
                                for line in path.read_text("utf-8").splitlines()
                                if line.strip() and not line.startswith("<!--")
                            ),
                            "—",
                        )[:100]
                table.add_row(
                    extension["id"],
                    extension["kind"],
                    extension["version"],
                    manifest["package"]["digest"][:12],
                    changelog,
                )
            console.print(table)
        elif selected == "4":
            table = Table(title="Update and rollback history", expand=True)
            for heading in ("Time", "Target", "From", "To", "Result"):
                table.add_column(heading)
            for item in service.store.update_history():
                table.add_row(
                    str(item["createdAt"]),
                    str(item["target"]),
                    str(item["fromVersion"] or "—"),
                    str(item["toVersion"] or "—"),
                    str(item["status"]),
                )
            console.print(table)
            console.print(
                "[dim]Use `buywell-edge module update|switch|rollback` after reviewing a package changelog.[/]"
            )
        else:
            if os.name == "nt":
                command = ["wevtutil", "qe", "Application", "/c:30", "/rd:true", "/f:text"]
            else:
                command = ["journalctl", "-u", "buywell-edge", "-n", "30", "--no-pager"]
            result = subprocess.run(command, check=False, capture_output=True, text=True)
            extension_logs = service.supervisor.recent_logs(30)
            console.print(
                Panel(
                    "\n".join(
                        value
                        for value in (
                            (result.stdout or result.stderr)[-8_000:],
                            extension_logs[-4_000:],
                        )
                        if value
                    )
                    or "No service logs are available.",
                    title="Recent redacted service logs",
                )
            )
        if not console.is_terminal:
            return
        selected = Prompt.ask(
            "Section (q to exit)",
            choices=[*sections, "q"],
            default=selected,
        )
        if selected == "q":
            return


@module_app.command("build")
def module_build(
    entrypoint: Annotated[str, typer.Argument(help="Python module:object declaration")],
    source: Annotated[Path, typer.Option("--source")] = Path("."),
    output: Annotated[Path, typer.Option("--output")] = Path("dist"),
    key: Annotated[Path, typer.Option("--key")] = Path(".buywell-edge/developer-key.pem"),
) -> None:
    module_name, _, object_name = entrypoint.partition(":")
    value = getattr(importlib.import_module(module_name), object_name)
    if not isinstance(value, ExtensionDefinition):
        raise typer.BadParameter("Entrypoint is not an Edge extension")
    signing_key = load_signing_key(key) if key.exists() else generate_signing_key(key)
    target = output / f"{value.extension_id}-{value.version}.buywell-edge.zip"
    inspected = build_package(value, source, target, signing_key=signing_key)
    console.print(f"[green]Built[/] {target}  sha256:{inspected.digest}")


@module_app.command("install")
def module_install(
    archive: Path,
    trust_key: Annotated[Path | None, typer.Option("--trust-key", help="Trusted Ed25519 public key (PEM or raw bytes)")] = None,
) -> None:
    service = _service()
    trusted: set[bytes] | None = None
    if trust_key:
        raw = trust_key.read_bytes()
        if raw.startswith(b"-----BEGIN"):
            public = serialization.load_pem_public_key(raw)
            if not isinstance(public, Ed25519PublicKey):
                raise typer.BadParameter("Trusted key must be Ed25519")
            raw = public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        trusted = {raw}
    inspected = service.packages.install(archive, trusted)
    extension = inspected.manifest["extension"]
    console.print(f"[green]Installed[/] {extension['id']} {extension['version']}")


@module_app.command("update")
def module_update(
    connection_id: str,
    archive: Path,
    trust_key: Annotated[Path | None, typer.Option("--trust-key")] = None,
) -> None:
    service = _service()
    trusted = None
    if trust_key:
        raw = trust_key.read_bytes()
        if raw.startswith(b"-----BEGIN"):
            public = serialization.load_pem_public_key(raw)
            if not isinstance(public, Ed25519PublicKey):
                raise typer.BadParameter("Trusted key must be Ed25519")
            raw = public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        trusted = {raw}
    inspected = service.packages.install(archive, trusted)
    extension = inspected.manifest["extension"]
    previous = service.store.switch_connection(connection_id, extension["version"], inspected.digest)
    console.print(f"[green]Switched[/] {connection_id} from {previous[0]} to {extension['version']}. The daemon will drain and restart the instance.")


@module_app.command("switch")
def module_switch(connection_id: str, version: str, digest: Annotated[str, typer.Option("--digest")]) -> None:
    previous = _service().store.switch_connection(connection_id, version, digest)
    console.print(f"[green]Switch scheduled.[/] Previous version: {previous[0]}")


@module_app.command("rollback")
def module_rollback(connection_id: str) -> None:
    version, _ = _service().store.rollback_connection(connection_id)
    console.print(f"[green]Rollback scheduled[/] to {version}.")


@module_app.command("remove")
def module_remove(extension_id: str, version: str, digest: Annotated[str, typer.Option("--digest")]) -> None:
    _service().packages.remove(extension_id, version, digest)
    console.print(f"[green]Removed[/] {extension_id} {version}")


@app.command("logs")
def logs(lines: Annotated[int, typer.Option("--lines", min=1, max=5000)] = 200) -> None:
    service = _service()
    if os.name == "nt":
        subprocess.run(["wevtutil", "qe", "Application", f"/c:{lines}", "/rd:true", "/f:text"], check=False)
    else:
        subprocess.run(["journalctl", "-u", "buywell-edge", "-n", str(lines), "--no-pager"], check=False)
    local = service.supervisor.recent_logs(lines)
    if local:
        console.print(Panel(local, title="Extension logs (redacted)"))


@app.command("update")
def edge_update(version: str, archive: Annotated[Path, typer.Option("--archive")]) -> None:
    manager = ReleaseManager(EdgeConfig.load().install_directory)
    manager.install(archive, version)
    previous = manager.switch(version)
    console.print(f"[green]Edge {version} is active.[/] Previous release: {previous or 'none'}. Restarting the service is safe at any time.")


@app.command("rollback")
def edge_rollback() -> None:
    version = ReleaseManager(EdgeConfig.load().install_directory).rollback()
    console.print(f"[green]Edge rollback activated[/] to {version}.")


migrate_app = typer.Typer(no_args_is_help=True)
app.add_typer(migrate_app, name="migrate")


@migrate_app.command("detect")
def migrate_detect() -> None:
    candidates = [Path.cwd() / "config.json", Path.cwd() / "data" / "config.json"]
    found = [str(path) for path in candidates if path.is_file()]
    console.print_json(data={"candidates": found})


@migrate_app.command("run")
def migrate_run(
    connection_id: str,
    source: Path,
    confirm: Annotated[
        bool,
        typer.Option("--yes", help="Confirm local import into this connection"),
    ] = False,
) -> None:
    if not source.is_file():
        raise typer.BadParameter("Legacy configuration was not found")
    if not confirm:
        raise typer.BadParameter(
            "Review the source path and pass --yes. The file is read locally and is never uploaded."
        )
    service = _service()
    connection = next(
        (item for item in service.store.connections() if item.id == connection_id),
        None,
    )
    if not connection:
        raise typer.BadParameter("Connection was not found")
    package = service.store.package(
        connection.extension_id,
        connection.extension_version,
        connection.package_digest,
    )
    if not package:
        raise typer.BadParameter("The exact connection package is not installed")
    manifest, _ = package
    try:
        raw = json.loads(source.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise typer.BadParameter("Legacy configuration must be a JSON object") from error
    if not isinstance(raw, dict):
        raise typer.BadParameter("Legacy configuration must be a JSON object")
    candidates = {
        **raw,
        **(raw.get("config") if isinstance(raw.get("config"), dict) else {}),
        **(raw.get("secrets") if isinstance(raw.get("secrets"), dict) else {}),
    }
    schema_fields = set(
        manifest.get("configuration", {}).get("schema", {}).get("properties", {})
    )
    secret_fields = set(
        manifest.get("configuration", {}).get("secretFields", [])
    )
    imported_secrets = {
        field: str(candidates[field])
        for field in secret_fields
        if candidates.get(field) not in (None, "")
    }
    imported_config = {
        field: candidates[field]
        for field in schema_fields - secret_fields
        if field in candidates
    }
    if not imported_secrets and not imported_config:
        raise typer.BadParameter(
            "The file has no fields recognized by this exact extension version"
        )
    secret_ref = connection.secret_ref or f"connection:{connection.id}"
    secrets = service.vault.get(secret_ref)
    service.vault.put(secret_ref, {**secrets, **imported_secrets})
    service.store.upsert_connection(
        replace(
            connection,
            config={**connection.config, **imported_config},
            secret_ref=secret_ref,
            health_state="offline",
            health_message=None,
        )
    )
    console.print(
        "[green]Imported locally.[/] "
        f"Configuration: {', '.join(sorted(imported_config)) or 'none'}; "
        f"secrets: {', '.join(sorted(imported_secrets)) or 'none'}. "
        "No values were sent to Buywell."
    )


@connection_app.command("add")
def connection_add(
    extension_id: str,
    version: str,
    digest: Annotated[str, typer.Option("--digest")],
    name: Annotated[str, typer.Option("--name")],
    kind: Annotated[str, typer.Option("--kind")] = "module",
    config_file: Annotated[Path | None, typer.Option("--config-file")] = None,
    secrets_file: Annotated[Path | None, typer.Option("--secrets-file", help="Read secrets from a protected local JSON file")] = None,
) -> None:
    service = _service()
    package = service.store.package(extension_id, version, digest)
    if not package:
        raise typer.BadParameter("Install the exact package before creating a connection")
    manifest, _ = package
    config = json.loads(config_file.read_text("utf-8")) if config_file else {}
    secrets = json.loads(secrets_file.read_text("utf-8")) if secrets_file else {
        field: typer.prompt(field.replace("_", " ").title(), hide_input=True)
        for field in manifest["configuration"].get("secretFields", [])
    }
    connection_id = str(uuid.uuid4())
    secret_ref = f"connection:{connection_id}"
    service.vault.put(secret_ref, {key: str(value) for key, value in secrets.items()})
    service.store.upsert_connection(ConnectionRecord(
        id=connection_id,
        extension_id=extension_id,
        extension_version=version,
        package_digest=digest,
        display_name=name,
        kind=kind,
        enabled=True,
        config=config,
        secret_ref=secret_ref,
        health_state="offline",
        health_message=None,
        session_expires_at=None,
        last_success_at=None,
    ))
    console.print(f"[green]Created[/] {name} ({connection_id})")


@connection_app.command("login")
def connection_login(
    connection_id: str,
    secrets_file: Annotated[Path | None, typer.Option("--secrets-file")] = None,
) -> None:
    service = _service()
    connection = next((item for item in service.store.connections() if item.id == connection_id), None)
    if not connection:
        raise typer.BadParameter("Connection was not found")
    package = service.store.package(connection.extension_id, connection.extension_version, connection.package_digest)
    if not package:
        raise typer.BadParameter("The connection package is not installed")
    manifest, _ = package
    secrets = json.loads(secrets_file.read_text("utf-8")) if secrets_file else {
        field: typer.prompt(field.replace("_", " ").title(), hide_input=True)
        for field in manifest["configuration"].get("secretFields", [])
    }
    service.vault.put(connection.secret_ref or f"connection:{connection_id}", {key: str(value) for key, value in secrets.items()})
    service.store.update_health(connection_id, {"state": "offline", "message": None})
    console.print("[green]Credentials updated locally.[/] Edge will verify the session.")


@connection_app.command("status")
def connection_status(connection_id: str) -> None:
    connection = next((item for item in _service().store.connections() if item.id == connection_id), None)
    if not connection:
        raise typer.BadParameter("Connection was not found")
    console.print_json(data={
        "id": connection.id,
        "name": connection.display_name,
        "extension": connection.extension_id,
        "version": connection.extension_version,
        "enabled": connection.enabled,
        "health": connection.health_state,
        "message": connection.health_message,
        "sessionExpiresAt": connection.session_expires_at,
        "lastSuccessAt": connection.last_success_at,
    })


@connection_app.command("enable")
def connection_enable(connection_id: str) -> None:
    if not _service().store.set_enabled(connection_id, True):
        raise typer.BadParameter("Connection was not found")


@connection_app.command("disable")
def connection_disable(connection_id: str) -> None:
    if not _service().store.set_enabled(connection_id, False):
        raise typer.BadParameter("Connection was not found")


if __name__ == "__main__":
    app()
