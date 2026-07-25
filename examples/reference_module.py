from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict

from buywell_edge_sdk import ActionContext, Health, HealthState, configuration_field, module


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prefix: str = configuration_field(
        label={"ru": "Префикс сообщения", "en": "Message prefix"},
        default="Buywell",
    )


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str


class Delivery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    delivered: bool
    text: str


extension = module(
    extension_id="example.market",
    version="1.0.0",
    display_name={"ru": "Пример площадки", "en": "Example marketplace"},
    description={"ru": "Нейтральный пример Edge-модуля.", "en": "Neutral Edge module example."},
    publisher="Buywell",
    entrypoint="reference_module:extension",
    config_model=Config,
    network_domains=["example.com"],
)


@extension.action(
    "send-message",
    "1.0.0",
    input_model=Message,
    output_model=Delivery,
    display_name={"ru": "Отправить сообщение", "en": "Send message"},
)
async def send_message(context: ActionContext, value: Message) -> Delivery:
    return Delivery(delivered=True, text=f"{context.config.get('prefix', 'Buywell')}: {value.text}")


@extension.health
async def health(_session) -> Health:
    return Health(state=HealthState.HEALTHY, last_success_at=datetime.now(timezone.utc).isoformat())
