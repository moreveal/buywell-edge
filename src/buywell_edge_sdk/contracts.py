from __future__ import annotations

import inspect
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Generic, Mapping, TypeVar

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

EDGE_PROTOCOL_VERSION = "2.0.0"
EDGE_MANIFEST_VERSION = 2
SDK_VERSION = "0.1.27"
IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
CONTRACT_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[._/-][a-z0-9]+)*$")
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$")
EXACT_DEPENDENCY = re.compile(r"[A-Za-z0-9_.-]+==[A-Za-z0-9_.+!-]+")
PINNED_SOURCE_DEPENDENCY = re.compile(
    r"[A-Za-z0-9_.-]+\s*@\s*https://github\.com/"
    r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/archive/[0-9a-f]{40}\.(?:zip|tar\.gz)"
)


class ExtensionKind(StrEnum):
    MODULE = "module"
    ADAPTER_DRIVER = "adapter-driver"


class ExecutionPolicy(StrEnum):
    EDGE = "edge"
    EDGE_REQUIRED = "edge_required"


class HealthState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    AUTH_REQUIRED = "auth_required"
    DISABLED = "disabled"
    OFFLINE = "offline"


class Health(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: HealthState
    message: str | None = Field(default=None, max_length=500)
    session_expires_at: str | None = None
    last_success_at: str | None = None


@dataclass(frozen=True)
class ActionContext:
    connection_id: str
    instance_id: str
    job_id: str
    idempotency_key: str
    state: Any
    config: dict[str, Any]
    secrets: dict[str, str]
    event_payload: dict[str, Any]
    event_scope: dict[str, Any]


@dataclass(frozen=True)
class AdapterContext(ActionContext):
    operation_id: str


class LocalizedText(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ru: str = Field(min_length=1, max_length=2_000)
    en: str = Field(min_length=1, max_length=2_000)


class LocalizedFiles(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ru: str
    en: str

    def model_post_init(self, _context: Any) -> None:
        for value in (self.ru, self.en):
            path = Path(value)
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in value
                or path.suffix.lower() != ".md"
            ):
                raise ValueError("Documentation paths must be safe relative Markdown paths")


class ContractSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    version: str
    display_name: LocalizedText
    description: LocalizedText | None = None
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]


class EventSpec(ContractSpec):
    identity_fields: list[str] = Field(min_length=1, max_length=20)
    scope_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})


class AdapterOperationSpec(ContractSpec):
    idempotency: str = "required"


class ConfigSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_: dict[str, Any] = Field(alias="schema")
    secret_fields: list[str] = Field(default_factory=list)


class PermissionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    network_domains: list[str] = Field(default_factory=list)
    local_paths: list[str] = Field(default_factory=list)
    subprocesses: bool = False


Handler = Callable[..., Awaitable[dict[str, Any]] | dict[str, Any]]
T = TypeVar("T", bound=BaseModel)


def _localized(value: dict[str, str] | LocalizedText) -> LocalizedText:
    return value if isinstance(value, LocalizedText) else LocalizedText.model_validate(value)


def configuration_field(
    *,
    label: dict[str, str] | LocalizedText,
    default: Any = PydanticUndefined,
    secret: bool = False,
    **kwargs: Any,
) -> FieldInfo:
    """Declare module-owned localized presentation for one configuration field."""
    extra = dict(kwargs.pop("json_schema_extra", {}) or {})
    extra["x-buywell-label"] = _localized(label).model_dump()
    if secret:
        extra["secret"] = True
    return Field(default=default, json_schema_extra=extra, **kwargs)


def contract_field(
    *,
    label: dict[str, str] | LocalizedText,
    default: Any = PydanticUndefined,
    **kwargs: Any,
) -> FieldInfo:
    """Declare localized presentation for operation input and output fields."""
    extra = dict(kwargs.pop("json_schema_extra", {}) or {})
    extra["x-buywell-label"] = _localized(label).model_dump()
    return Field(default=default, json_schema_extra=extra, **kwargs)


def _validate_identity(value: str, field_name: str) -> None:
    pattern = SEMVER if field_name == "version" else CONTRACT_IDENTIFIER if field_name in ("event id", "action id", "operation id") else IDENTIFIER
    if not pattern.fullmatch(value):
        raise ValueError(f"Invalid {field_name}: {value}")


def _schema(model: type[BaseModel] | None) -> dict[str, Any]:
    if model is None:
        return {"type": "object", "properties": {}, "additionalProperties": False}
    schema = model.model_json_schema(mode="serialization")
    schema.pop("title", None)
    return schema


def _secret_fields(model: type[BaseModel] | None) -> list[str]:
    if model is None:
        return []
    return sorted(
        name
        for name, info in model.model_fields.items()
        if info.annotation is SecretStr or "secret" in (info.json_schema_extra or {})
    )


@dataclass
class ExtensionDefinition:
    kind: ExtensionKind
    extension_id: str
    version: str
    display_name: LocalizedText
    publisher: str
    entrypoint: str
    description: LocalizedText | None = None
    config_model: type[BaseModel] | None = None
    permissions: PermissionSpec = field(default_factory=PermissionSpec)
    dependencies: list[str] = field(default_factory=list)
    events: list[EventSpec] = field(default_factory=list)
    actions: list[ContractSpec] = field(default_factory=list)
    operations: list[AdapterOperationSpec] = field(default_factory=list)
    handlers: dict[str, Handler] = field(default_factory=dict, repr=False)
    protocol_handlers: dict[str, Handler] = field(default_factory=dict, repr=False)
    health_handler: Handler | None = field(default=None, repr=False)
    start_handler: Handler | None = field(default=None, repr=False)
    stop_handler: Handler | None = field(default=None, repr=False)
    migration_handler: Handler | None = field(default=None, repr=False)
    legacy_module_manifest: dict[str, Any] | None = field(default=None, repr=False)
    guides: LocalizedFiles | None = None
    changelog: LocalizedFiles | None = None
    managed_adapter: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        _validate_identity(self.extension_id, "extension id")
        _validate_identity(self.version, "version")
        if not self.entrypoint or ":" not in self.entrypoint:
            raise ValueError("Entrypoint must use module:object syntax")
        for dependency in self.dependencies:
            if not (
                EXACT_DEPENDENCY.fullmatch(dependency)
                or PINNED_SOURCE_DEPENDENCY.fullmatch(dependency)
            ):
                raise ValueError(
                    "Extension dependencies must use an exact name==version pin "
                    "or an HTTPS GitHub archive URL pinned to a full commit SHA"
                )

    def event(
        self,
        event_id: str,
        version: str,
        *,
        payload_model: type[BaseModel],
        scope_model: type[BaseModel] | None = None,
        identity_fields: list[str],
        display_name: dict[str, str] | LocalizedText | None = None,
        description: dict[str, str] | LocalizedText | None = None,
    ) -> Callable[[Handler], Handler]:
        _validate_identity(event_id, "event id")
        _validate_identity(version, "version")

        def decorate(handler: Handler) -> Handler:
            self.events.append(EventSpec(
                id=event_id,
                version=version,
                display_name=_localized(display_name or {"ru": event_id, "en": event_id}),
                description=_localized(description) if description else None,
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                output_schema=_schema(payload_model),
                scope_schema=_schema(scope_model),
                identity_fields=identity_fields,
            ))
            self.handlers[f"event:{event_id}@{version}"] = handler
            return handler

        return decorate

    def action(
        self,
        action_id: str,
        version: str,
        *,
        input_model: type[BaseModel] | None = None,
        output_model: type[BaseModel] | None = None,
        display_name: dict[str, str] | LocalizedText | None = None,
        description: dict[str, str] | LocalizedText | None = None,
    ) -> Callable[[Handler], Handler]:
        _validate_identity(action_id, "action id")
        _validate_identity(version, "version")

        def decorate(handler: Handler) -> Handler:
            self.actions.append(ContractSpec(
                id=action_id,
                version=version,
                display_name=_localized(display_name or {"ru": action_id, "en": action_id}),
                description=_localized(description) if description else None,
                input_schema=_schema(input_model),
                output_schema=_schema(output_model),
            ))
            self.handlers[f"action:{action_id}@{version}"] = _typed_handler(handler, input_model, output_model)
            return handler

        return decorate

    def operation(
        self,
        operation_id: str,
        version: str,
        *,
        input_model: type[BaseModel] | None = None,
        output_model: type[BaseModel] | None = None,
        display_name: dict[str, str] | LocalizedText | None = None,
        description: dict[str, str] | LocalizedText | None = None,
        idempotency: str = "required",
    ) -> Callable[[Handler], Handler]:
        if self.kind is not ExtensionKind.ADAPTER_DRIVER:
            raise ValueError("Adapter operations are available only to adapter drivers")
        _validate_identity(operation_id, "operation id")
        _validate_identity(version, "version")

        def decorate(handler: Handler) -> Handler:
            self.operations.append(AdapterOperationSpec(
                id=operation_id,
                version=version,
                display_name=_localized(display_name or {"ru": operation_id, "en": operation_id}),
                description=_localized(description) if description else None,
                input_schema=_schema(input_model),
                output_schema=_schema(output_model),
                idempotency=idempotency,
            ))
            self.handlers[f"operation:{operation_id}@{version}"] = _typed_handler(handler, input_model, output_model)
            return handler

        return decorate

    def health(self, handler: Handler) -> Handler:
        self.health_handler = handler
        return handler

    def binding_catalog(self, catalog_id: str, version: str) -> Callable[[Handler], Handler]:
        _validate_identity(catalog_id, "operation id")
        _validate_identity(version, "version")

        def decorate(handler: Handler) -> Handler:
            self.protocol_handlers[f"binding-catalog:{catalog_id}@{version}"] = handler
            return handler

        return decorate

    def input_resolver(self, resolver_id: str, version: str) -> Callable[[Handler], Handler]:
        _validate_identity(resolver_id, "operation id")
        _validate_identity(version, "version")

        def decorate(handler: Handler) -> Handler:
            self.protocol_handlers[f"input-resolver:{resolver_id}@{version}"] = handler
            return handler

        return decorate

    def on_start(self, handler: Handler) -> Handler:
        self.start_handler = handler
        return handler

    def on_stop(self, handler: Handler) -> Handler:
        self.stop_handler = handler
        return handler

    def migration(self, handler: Handler) -> Handler:
        """Register a local state migration `(session, from_version, to_version)`."""
        self.migration_handler = handler
        return handler

    def manifest(self) -> dict[str, Any]:
        contracts: dict[str, Any] = {}
        if self.events:
            contracts["events"] = [item.model_dump(by_alias=True, exclude_none=True) for item in self.events]
        if self.actions:
            contracts["actions"] = [item.model_dump(by_alias=True, exclude_none=True) for item in self.actions]
        if self.operations:
            contracts["adapterOperations"] = [item.model_dump(by_alias=True, exclude_none=True) for item in self.operations]
        config = ConfigSpec(schema=_schema(self.config_model), secret_fields=_secret_fields(self.config_model))
        manifest = {
            "schemaVersion": EDGE_MANIFEST_VERSION,
            "protocolVersion": EDGE_PROTOCOL_VERSION,
            "extension": {
                "kind": self.kind.value,
                "id": self.extension_id,
                "version": self.version,
                "displayName": self.display_name.model_dump(),
                **({"description": self.description.model_dump()} if self.description else {}),
                "publisher": self.publisher,
            },
            "runtime": {
                "language": "python",
                "python": ">=3.12,<3.13",
                "sdk": f">={SDK_VERSION},<1",
                "entrypoint": self.entrypoint,
                "dependencies": sorted(set(self.dependencies), key=str.lower),
            },
            "executionPolicy": ExecutionPolicy.EDGE_REQUIRED.value if self.kind is ExtensionKind.ADAPTER_DRIVER else ExecutionPolicy.EDGE.value,
            "configuration": {
                "schema": config.schema_,
                "secretFields": config.secret_fields,
            },
            "permissions": self.permissions.model_dump(by_alias=True),
            "contracts": contracts,
        }
        if self.guides or self.changelog:
            manifest["documentation"] = {
                **(
                    {"guides": self.guides.model_dump()}
                    if self.guides
                    else {}
                ),
                **(
                    {"changelog": self.changelog.model_dump()}
                    if self.changelog
                    else {}
                ),
            }
        if self.legacy_module_manifest is not None:
            manifest["compatibility"] = {
                "moduleManifest": self.legacy_module_manifest,
                "contractMode": "preserve-v1",
            }
        if self.managed_adapter is not None:
            manifest["managedAdapter"] = json.loads(json.dumps(self.managed_adapter))
        return manifest


def _typed_handler(
    handler: Handler,
    input_model: type[T] | None,
    output_model: type[BaseModel] | None,
) -> Handler:
    async def wrapped(context: ActionContext, raw: dict[str, Any]) -> dict[str, Any]:
        value: Any = input_model.model_validate(raw) if input_model else raw
        result = handler(context, value)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, BaseModel):
            payload = result.model_dump(mode="json")
        elif isinstance(result, dict):
            payload = result
        else:
            raise TypeError("Extension handlers must return a mapping or Pydantic model")
        if output_model is None:
            return payload
        return output_model.model_validate(payload).model_dump(mode="json")

    return wrapped


def module(
    *,
    extension_id: str,
    version: str,
    display_name: dict[str, str],
    publisher: str,
    entrypoint: str,
    description: dict[str, str] | None = None,
    config_model: type[BaseModel] | None = None,
    network_domains: list[str] | None = None,
    dependencies: list[str] | None = None,
    legacy_manifest: Mapping[str, Any] | Path | str | None = None,
    guides: dict[str, str] | None = None,
    changelog: dict[str, str] | None = None,
) -> ExtensionDefinition:
    preserved: dict[str, Any] | None = None
    if legacy_manifest is not None:
        if isinstance(legacy_manifest, (Path, str)):
            preserved = json.loads(Path(legacy_manifest).read_text("utf-8"))
        else:
            preserved = json.loads(json.dumps(legacy_manifest))
        legacy_module = preserved.get("module")
        if not isinstance(legacy_module, dict):
            raise ValueError("Legacy manifest must contain a module object")
        if legacy_module.get("id") != extension_id or legacy_module.get("version") != version:
            raise ValueError("Legacy manifest identity must match the Edge extension")
    return ExtensionDefinition(
        kind=ExtensionKind.MODULE,
        extension_id=extension_id,
        version=version,
        display_name=_localized(display_name),
        publisher=publisher,
        entrypoint=entrypoint,
        description=_localized(description) if description else None,
        config_model=config_model,
        permissions=PermissionSpec(network_domains=network_domains or []),
        dependencies=dependencies or [],
        legacy_module_manifest=preserved,
        guides=LocalizedFiles.model_validate(guides) if guides else None,
        changelog=LocalizedFiles.model_validate(changelog) if changelog else None,
    )


def adapter_driver(
    *,
    extension_id: str,
    version: str,
    display_name: dict[str, str],
    publisher: str,
    entrypoint: str,
    description: dict[str, str] | None = None,
    config_model: type[BaseModel] | None = None,
    network_domains: list[str] | None = None,
    dependencies: list[str] | None = None,
    guides: dict[str, str] | None = None,
    changelog: dict[str, str] | None = None,
    adapter_version: str | None = None,
    adapter_dsl_namespace: str | None = None,
    adapter_definition_revision: int = 1,
) -> ExtensionDefinition:
    managed_version = adapter_version or version
    _validate_identity(managed_version, "version")
    namespace = adapter_dsl_namespace or extension_id.removeprefix("adapter.").replace(".", "_").replace("-", "_")
    if not re.fullmatch(r"^[a-z][a-z0-9_]{0,63}$", namespace):
        raise ValueError("Adapter DSL namespace must be a lowercase identifier")
    if adapter_definition_revision < 1:
        raise ValueError("Adapter definition revision must be positive")
    return ExtensionDefinition(
        kind=ExtensionKind.ADAPTER_DRIVER,
        extension_id=extension_id,
        version=version,
        display_name=_localized(display_name),
        publisher=publisher,
        entrypoint=entrypoint,
        description=_localized(description) if description else None,
        config_model=config_model,
        permissions=PermissionSpec(network_domains=network_domains or []),
        dependencies=dependencies or [],
        guides=LocalizedFiles.model_validate(guides) if guides else None,
        changelog=LocalizedFiles.model_validate(changelog) if changelog else None,
        managed_adapter={
            "moduleVersion": managed_version,
            "dslNamespace": namespace,
            "definitionRevision": adapter_definition_revision,
        },
    )
