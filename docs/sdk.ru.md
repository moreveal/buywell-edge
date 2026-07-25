# SDK Buywell Edge

Официальные и частные расширения используют один пакет
`buywell-edge-sdk`. Модуль публикует события и действия площадки,
`adapter-driver` исполняет методы конкретного API. SDK берёт на себя framed
stdio protocol; расширению не нужны WebSocket, ключ Buywell, heartbeat, leases,
outbox или повторная доставка.

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

SDK добавляет в подписанный manifest секцию `managedAdapter`. После создания
подключения Edge передаёт Buywell полный manifest и список
`adapterOperations`. Buywell заново проверяет digest и Ed25519-подпись, затем
создаёт для этого аккаунта блоки и поля из Pydantic-схем. Отдельно описывать тот
же адаптер на сайте не требуется. Пакет другого автора не получает подпись
Buywell и не становится общим для остальных аккаунтов.

Если версия адаптера должна отличаться от версии драйвера, передайте
`adapter_version`, `adapter_dsl_namespace` и
`adapter_definition_revision` в `adapter_driver(...)`.

`SecretStr` и поля с JSON Schema metadata `secret` автоматически попадают в
`configuration.secretFields`. Pydantic-модели становятся JSON Schema.
Зависимости указываются только точными pin-версиями `name==version`. Если
библиотеки нет в PyPI, разрешён immutable GitHub archive URL с полным
40-символьным commit SHA. В `guides` и `changelog` указываются RU/EN Markdown
файлы; сборщик проверит, что оба файла действительно вошли в пакет.

Сборка:

```bash
buywell-edge module build driver:driver --source .
```

Первая сборка создаёт локальный Ed25519 developer key. Повторная сборка тех же
исходников создаёт идентичный архив и digest. Production Edge принимает
подписанные пакеты доверенных publishers; unsigned разрешены только в local dev
mode.

## Перенос существующего модуля

Передайте `legacy_manifest=Path("manifest.json")` в `module(...)`. SDK вложит
канонический manifest v1 с режимом `preserve-v1`. ID и версии событий, actions,
catalogs, resolvers и abstractions не меняются. Edge package получает отдельную
версию и digest, поэтому опубликованные workflow revisions переписывать не
нужно.

Это также не меняет пользовательскую авторизацию Buywell v1: старый connection
key продолжает работать у неизменяемого legacy runtime до явной миграции. В
Edge provider-сессия импортируется локально с согласия пользователя, а device
credential выдаётся отдельным одноразовым pairing-потоком. Provider secrets и
старый Buywell key не отправляются через manifest и не переносятся сервером.

Provider implementation можно обновлять отдельно, в том числе переносить
проверенные изменения из закреплённого upstream. Любое изменение публичного
контракта всё равно требует новой версии модуля и обычных contract tests.
