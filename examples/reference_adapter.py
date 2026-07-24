from __future__ import annotations

from pydantic import BaseModel, ConfigDict, SecretStr

from buywell_edge_sdk import AdapterContext, Health, HealthState, adapter_driver


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account: str
    api_key: SecretStr


class ReserveInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sku: str
    quantity: int


class ReserveOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reservation_id: str


extension = adapter_driver(
    extension_id="example.supplier",
    version="1.0.0",
    display_name={"ru": "Пример поставщика", "en": "Example supplier"},
    publisher="Buywell",
    entrypoint="reference_adapter:extension",
    config_model=Config,
    network_domains=["api.example.com"],
)


@extension.operation(
    "reserve",
    "1.0.0",
    input_model=ReserveInput,
    output_model=ReserveOutput,
    display_name={"ru": "Зарезервировать", "en": "Reserve"},
)
async def reserve(context: AdapterContext, value: ReserveInput) -> ReserveOutput:
    return ReserveOutput(reservation_id=f"{value.sku}:{context.idempotency_key[:12]}")


@extension.health
async def health(session) -> Health:
    return Health(state=HealthState.HEALTHY if session.secrets.get("api_key") else HealthState.AUTH_REQUIRED)
