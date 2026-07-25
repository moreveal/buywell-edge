from __future__ import annotations

import asyncio
import importlib
import json
import os
import platform
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Annotated

import httpx
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
from .official_packages import OFFICIAL_PACKAGES, official_package, verify_archive
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


def _locale(service: EdgeService) -> str:
    paired = service.store.metadata("locale")
    if paired in {"ru", "en"}:
        return paired
    return "ru" if os.environ.get("LANG", "").lower().startswith("ru") else "en"


def _message(service: EdgeService, ru: str, en: str) -> str:
    return en if _locale(service) == "en" else ru


def _version_key(value: str) -> tuple[int, int, int, str]:
    base, _, suffix = value.partition("-")
    parts = base.split(".")
    numbers = tuple(int(part) if part.isdigit() else 0 for part in parts[:3])
    return (*numbers, suffix or "\uffff")  # type: ignore[return-value]


def _localized_name(manifest: dict[str, object], locale: str) -> str:
    extension = manifest["extension"]
    assert isinstance(extension, dict)
    names = extension.get("displayName")
    if isinstance(names, dict):
        value = names.get(locale) or names.get("en") or names.get("ru")
        if isinstance(value, str):
            return value
    return str(extension["id"])


def _coerce_value(value: str, schema: dict[str, object]) -> object:
    value_type = schema.get("type")
    if value_type == "boolean":
        return value.strip().lower() in {"1", "true", "yes", "y", "да"}
    if value_type == "integer":
        return int(value)
    if value_type == "number":
        return float(value)
    return value


def _prompt_value(label: str, schema: dict[str, object], default: object) -> object:
    rendered_default = None if default is None else str(default)
    return _coerce_value(Prompt.ask(label, default=rendered_default), schema)


def _field_label(field: str, schema: dict[str, object], locale: str) -> str:
    labels = schema.get("x-buywell-label")
    if isinstance(labels, dict):
        localized = labels.get(locale) or labels.get("en") or labels.get("ru")
        if isinstance(localized, str) and localized.strip():
            return localized
    return str(schema.get("title") or field.replace("_", " ").title())


def _is_secret_schema(schema: dict[str, object]) -> bool:
    if schema.get("writeOnly") is True:
        return True
    alternatives = schema.get("anyOf")
    return isinstance(alternatives, list) and any(
        isinstance(item, dict) and item.get("writeOnly") is True
        for item in alternatives
    )


def _resolve_connection(
    service: EdgeService,
    reference: str | None,
) -> ConnectionRecord:
    connections = service.store.connections()
    if not connections:
        raise typer.BadParameter(
            _message(service, "Подключений пока нет", "There are no connections yet")
        )
    if reference:
        normalized = reference.casefold()
        connections = [
            item for item in connections
            if normalized in {
                item.id.casefold(),
                item.display_name.casefold(),
                item.extension_id.casefold(),
            } or item.id.casefold().startswith(normalized)
        ]
        if not connections:
            raise typer.BadParameter(
                _message(
                    service,
                    f"Подключение «{reference}» не найдено",
                    f"Connection “{reference}” was not found",
                )
            )
    if len(connections) == 1:
        return connections[0]
    if not console.is_terminal:
        names = ", ".join(item.display_name for item in connections)
        raise typer.BadParameter(
            _message(
                service,
                f"Найдено несколько подключений: {names}. Укажите имя.",
                f"Multiple connections matched: {names}. Specify a name.",
            )
        )
    choices = {
        str(index): item for index, item in enumerate(connections, start=1)
    }
    console.print(_message(service, "Выберите аккаунт:", "Select an account:"))
    for index, item in choices.items():
        console.print(
            f"  {index}. {item.display_name} ({item.extension_id} · {item.health_state})"
        )
    selected = Prompt.ask(
        _message(service, "Номер", "Number"),
        choices=list(choices),
        default="1",
    )
    return choices[selected]


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
    package: Annotated[
        str,
        typer.Argument(
            help="Official module reference (for example funpay.cardinal@1.3.0) or a local package path"
        ),
    ],
    trust_key: Annotated[Path | None, typer.Option("--trust-key", help="Trusted Ed25519 public key (PEM or raw bytes)")] = None,
) -> None:
    service = _service()
    trusted: set[bytes] | None = None
    temporary_path: Path | None = None
    if trust_key:
        raw = trust_key.read_bytes()
        if raw.startswith(b"-----BEGIN"):
            public = serialization.load_pem_public_key(raw)
            if not isinstance(public, Ed25519PublicKey):
                raise typer.BadParameter("Trusted key must be Ed25519")
            raw = public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        trusted = {raw}
    archive = Path(package)
    official = official_package(package)
    if official:
        if trust_key:
            raise typer.BadParameter("--trust-key is only used with local package files")
        url = f"{service.config.buywell_url}/edge/packages/{official.filename}"
        try:
            response = httpx.get(url, follow_redirects=True, timeout=60)
            response.raise_for_status()
            verify_archive(official, response.content)
        except (httpx.HTTPError, ValueError) as error:
            raise typer.BadParameter(
                f"Could not download the official package {official.reference}: {error}"
            ) from error
        handle, name = tempfile.mkstemp(suffix=".buywell-edge.zip")
        os.close(handle)
        temporary_path = Path(name)
        temporary_path.write_bytes(response.content)
        archive = temporary_path
        trusted = {official.public_key}
    elif not archive.is_file():
        examples = ", ".join(OFFICIAL_PACKAGES)
        raise typer.BadParameter(
            f"Package file was not found: {package}. "
            f"Use an official reference ({examples}) or an existing local ZIP file."
        )
    try:
        inspected = service.packages.install(archive, trusted)
        extension = inspected.manifest["extension"]
        console.print(
            _message(
                service,
                (
                    f"[green]Установлен модуль[/] {_localized_name(inspected.manifest, 'ru')} "
                    f"{extension['version']}.\n"
                    f"Дальше запустите локальный мастер: "
                    f"`buywell-edge connection add {extension['id']}`"
                ),
                (
                    f"[green]Installed[/] {_localized_name(inspected.manifest, 'en')} "
                    f"{extension['version']}.\n"
                    f"Next, run the local setup wizard: "
                    f"`buywell-edge connection add {extension['id']}`"
                ),
            )
        )
    finally:
        if temporary_path:
            temporary_path.unlink(missing_ok=True)


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
def edge_update(
    version: Annotated[str, typer.Option("--version", help="Release version or latest")] = "latest",
    archive: Annotated[Path | None, typer.Option("--archive", help="Use a local release archive")] = None,
) -> None:
    config = EdgeConfig.load()
    service = EdgeService(config)
    if os.name != "nt" and os.geteuid() != 0:
        raise typer.BadParameter(
            _message(
                service,
                "Запустите `sudo buywell-edge update`",
                "Run `sudo buywell-edge update`",
            )
        )
    manager = ReleaseManager(config.install_directory)
    temporary: Path | None = None
    try:
        if archive is None:
            resolved_version, temporary = manager.download(version)
            archive = temporary
        else:
            resolved_version = version.removeprefix("v")
            if resolved_version == "latest":
                raise typer.BadParameter("--version is required with --archive")
        if resolved_version == __version__:
            console.print(
                "[green]"
                + _message(
                    service,
                    f"Buywell Edge {resolved_version} уже обновлён.",
                    f"Buywell Edge {resolved_version} is already current.",
                )
                + "[/]"
            )
            return
        manager.install(archive, resolved_version)
        previous = manager.switch(resolved_version)
        if os.name != "nt":
            service_file = manager.releases / resolved_version / "share" / "buywell-edge.service"
            shutil.copy2(service_file, "/etc/systemd/system/buywell-edge.service")
            executable = Path("/usr/local/bin/buywell-edge")
            executable.unlink(missing_ok=True)
            executable.symlink_to(config.install_directory / "current" / "bin" / "buywell-edge")
            subprocess.run(["systemctl", "daemon-reload"], check=True)
            subprocess.run(["systemctl", "restart", "buywell-edge"], check=True)
            for _ in range(40):
                active = subprocess.run(
                    ["systemctl", "is-active", "--quiet", "buywell-edge"],
                    check=False,
                )
                if active.returncode == 0:
                    break
                time.sleep(0.25)
            else:
                subprocess.run(
                    ["systemctl", "status", "buywell-edge", "--no-pager"],
                    check=False,
                )
                raise RuntimeError(
                    _message(
                        service,
                        "Обновление установлено, но сервис Buywell Edge не запустился.",
                        "The update was installed, but the Buywell Edge service did not start.",
                    )
                )
        manager.prune({resolved_version, *([previous] if previous else [])})
        console.print(
            "[green]"
            + _message(
                service,
                f"Buywell Edge обновлён до {resolved_version}. Сервис перезапускается в фоне; привязка и аккаунты сохранены.",
                f"Buywell Edge updated to {resolved_version}. The service is restarting in the background; pairing and accounts were preserved.",
            )
            + "[/]"
        )
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


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
    extension_id: Annotated[str | None, typer.Argument(help="Installed module ID")] = None,
    version: Annotated[str | None, typer.Argument(help="Installed module version")] = None,
    digest: Annotated[str | None, typer.Option("--digest", help="Exact package digest for automation")] = None,
    name: Annotated[str | None, typer.Option("--name")] = None,
    kind: Annotated[str | None, typer.Option("--kind")] = None,
    config_file: Annotated[Path | None, typer.Option("--config-file")] = None,
    secrets_file: Annotated[Path | None, typer.Option("--secrets-file", help="Read secrets from a protected local JSON file")] = None,
) -> None:
    service = _service()
    locale = _locale(service)
    installed = service.store.installed_packages()
    candidates = [
        item for item in installed
        if extension_id is None or item[0]["extension"]["id"] == extension_id
    ]
    if version is not None:
        candidates = [
            item for item in candidates
            if item[0]["extension"]["version"] == version
        ]
    if digest is not None:
        candidates = [
            item for item in candidates
            if item[0]["package"]["digest"] == digest
        ]
    if not candidates:
        raise typer.BadParameter(
            _message(
                service,
                "Подходящий модуль не установлен. Сначала выполните `buywell-edge module install <модуль>@<версия>`.",
                "The requested module is not installed. Run `buywell-edge module install <module>@<version>` first.",
            )
        )
    candidates.sort(
        key=lambda item: (
            str(item[0]["extension"]["id"]),
            _version_key(str(item[0]["extension"]["version"])),
        ),
        reverse=True,
    )
    if extension_id is None and len({item[0]["extension"]["id"] for item in candidates}) > 1:
        choices = {
            str(index): item
            for index, item in enumerate(candidates, start=1)
        }
        console.print(_message(service, "Выберите площадку:", "Select a platform:"))
        for index, (manifest, _) in choices.items():
            console.print(
                f"  {index}. {_localized_name(manifest, locale)} "
                f"({manifest['extension']['id']} · {manifest['extension']['version']})"
            )
        selected = Prompt.ask(
            _message(service, "Номер", "Number"),
            choices=list(choices),
            default="1",
        )
        manifest, _ = choices[selected]
    else:
        manifest, _ = candidates[0]
    extension = manifest["extension"]
    extension_id = str(extension["id"])
    version = str(extension["version"])
    digest = str(manifest["package"]["digest"])
    kind = kind or str(extension["kind"])
    display_name = _localized_name(manifest, locale)
    same_provider = [
        item for item in service.store.connections()
        if item.extension_id == extension_id
    ]
    default_name = display_name if not same_provider else f"{display_name} {len(same_provider) + 1}"
    if name is None:
        name = Prompt.ask(
            _message(service, "Название аккаунта", "Account name"),
            default=default_name,
        )

    configuration = manifest.get("configuration") or {}
    schema = configuration.get("schema") or {}
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    secret_fields = list(configuration.get("secretFields") or [])
    secret_fields = list(dict.fromkeys([
        *secret_fields,
        *[
            field for field, field_schema in properties.items()
            if _is_secret_schema(field_schema)
        ],
    ]))
    config = json.loads(config_file.read_text("utf-8")) if config_file else {}
    if not config_file:
        for field, field_schema in properties.items():
            if field in secret_fields:
                continue
            default = field_schema.get("default")
            label = _field_label(field, field_schema, locale)
            if field not in required and default is None:
                optional = Prompt.ask(
                    f"{label} ({'необязательно' if locale == 'ru' else 'optional'})",
                    default="",
                )
                if optional:
                    config[field] = optional
                continue
            config[field] = _prompt_value(label, field_schema, default)
    if secrets_file:
        secrets = json.loads(secrets_file.read_text("utf-8"))
    else:
        secrets = {}
        for field in secret_fields:
            field_schema = properties.get(field) or {}
            label = _field_label(field, field_schema, locale)
            optional = field not in required
            value = typer.prompt(
                f"{label} ({'необязательно' if locale == 'ru' else 'optional'})"
                if optional else label,
                default="" if optional else None,
                hide_input=True,
                show_default=False,
            )
            if value:
                secrets[field] = value
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
    console.print(
        _message(
            service,
            (
                f"[green]Аккаунт подключён:[/] {name}.\n"
                "Edge запустит его автоматически; статус появится в Buywell через несколько секунд."
            ),
            (
                f"[green]Account connected:[/] {name}.\n"
                "Edge will start it automatically; its status will appear in Buywell within a few seconds."
            ),
        )
    )


@connection_app.command("login")
def connection_login(
    connection: Annotated[str | None, typer.Argument(help="Account name, module ID, or connection ID")] = None,
    secrets_file: Annotated[Path | None, typer.Option("--secrets-file")] = None,
) -> None:
    service = _service()
    selected = _resolve_connection(service, connection)
    package = service.store.package(selected.extension_id, selected.extension_version, selected.package_digest)
    if not package:
        raise typer.BadParameter(
            _message(
                service,
                "Пакет этого подключения не установлен",
                "The connection package is not installed",
            )
        )
    manifest, _ = package
    locale = _locale(service)
    configuration = manifest.get("configuration") or {}
    schema = configuration.get("schema") or {}
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    secret_fields = set(configuration.get("secretFields") or [])
    config = dict(selected.config)
    console.print(
        _message(
            service,
            "Нажмите Enter, чтобы оставить текущее значение.",
            "Press Enter to keep the current value.",
        )
    )
    for field, field_schema in properties.items():
        if field in secret_fields or _is_secret_schema(field_schema):
            continue
        label = _field_label(field, field_schema, locale)
        current = config.get(field, field_schema.get("default"))
        value = Prompt.ask(label, default="" if current is None else str(current))
        if value:
            config[field] = _coerce_value(value, field_schema)
        elif field not in required and current is None:
            config.pop(field, None)

    previous_secrets = service.vault.get(selected.secret_ref)
    secrets = dict(previous_secrets)
    if secrets_file:
        secrets.update(json.loads(secrets_file.read_text("utf-8")))
    else:
        for field in secret_fields:
            field_schema = properties.get(field) or {}
            label = _field_label(field, field_schema, locale)
            value = typer.prompt(
                f"{label} ({'Enter — оставить текущее' if locale == 'ru' else 'Enter to keep current'})",
                default="",
                hide_input=True,
                show_default=False,
            )
            if value:
                secrets[field] = value
    secret_ref = f"connection:{selected.id}:{uuid.uuid4()}"
    service.vault.put(secret_ref, {key: str(value) for key, value in secrets.items()})
    service.store.upsert_connection(
        replace(
            selected,
            config=config,
            secret_ref=secret_ref,
            health_state="offline",
            health_message=None,
        )
    )
    if selected.secret_ref:
        service.vault.delete(selected.secret_ref)
    console.print(
        _message(
            service,
            "[green]Данные обновлены локально.[/] Edge перезапустит аккаунт и проверит авторизацию.",
            "[green]Credentials updated locally.[/] Edge will restart the account and verify authentication.",
        )
    )


@connection_app.command("status")
def connection_status(
    connection: Annotated[str | None, typer.Argument(help="Account name, module ID, or connection ID")] = None,
) -> None:
    service = _service()
    selected = _resolve_connection(service, connection)
    console.print_json(data={
        "name": selected.display_name,
        "extension": selected.extension_id,
        "version": selected.extension_version,
        "enabled": selected.enabled,
        "health": selected.health_state,
        "message": selected.health_message,
        "sessionExpiresAt": selected.session_expires_at,
        "lastSuccessAt": selected.last_success_at,
    })
    if selected.health_state == "auth_required":
        console.print(
            _message(
                service,
                f"Повторите вход: `buywell-edge connection login \"{selected.display_name}\"`",
                f"Sign in again: `buywell-edge connection login \"{selected.display_name}\"`",
            )
        )


@connection_app.command("enable")
def connection_enable(
    connection: Annotated[str | None, typer.Argument(help="Account name, module ID, or connection ID")] = None,
) -> None:
    service = _service()
    selected = _resolve_connection(service, connection)
    service.store.set_enabled(selected.id, True)


@connection_app.command("disable")
def connection_disable(
    connection: Annotated[str | None, typer.Argument(help="Account name, module ID, or connection ID")] = None,
) -> None:
    service = _service()
    selected = _resolve_connection(service, connection)
    service.store.set_enabled(selected.id, False)


if __name__ == "__main__":
    app()
