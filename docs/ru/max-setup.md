# Подключение MAX к Hermes Agent

Это инструкция для текущего MVP. Плагин не обещает универсальную
доступность MAX при любых региональных ограничениях связи: результат зависит
от региона, оператора, устройства и режима ограничения.

## 1. Создание бота

Используйте актуальный процесс MAX для бизнеса и получите Bot API token.
Старые инструкции про `@MasterBot` и `/newbot` намеренно не используются:
правила регистрации и верификации MAX менялись.

Официальные страницы:

- https://dev.max.ru/docs/chatbots/bots-coding/prepare
- https://dev.max.ru/docs-api

## 2. Установка без изменения Hermes core

Рекомендуемый способ для Hermes Agent — установить подкаталог напрямую из
GitHub. Корень этого репозитория содержит два плагина и не устанавливается как
один plugin:

```bash
hermes plugins install dimasalmin/hermes-agent-ru-messengers/plugins/max --enable
```

После установки проверьте plugin и перезапустите gateway:

```bash
hermes plugins list --user
hermes doctor
hermes gateway restart
```

Для разработки из локального checkout репозиторий должен оставаться отдельным
от Hermes:

```bash
python -m pip install -e ".[dev]"
ln -s "/path/to/hermes-agent-ru-messengers/plugins/max" "$HOME/.hermes/plugins/max"
```

В Windows вместо `ln -s` используйте junction или каталог-копию. Внутри
установленного каталога должен быть файл `plugin.yaml` в нижнем регистре.
Файлы Hermes `gateway/`, `agent/` и `hermes_cli/` изменять не нужно.

## 3. Минимальная конфигурация

```env
MAX_BOT_TOKEN=<token MAX для бизнеса>
MAX_ALLOWED_USERS=<числовые user_id через запятую>
# Необязательно: максимум одного входящего/исходящего файла, 50 MiB по умолчанию.
MAX_MEDIA_MAX_BYTES=52428800
```

Если `MAX_WEBHOOK_URL` не задан, используется Long Polling. Это режим для
разработки и smoke-тестов. Для production MAX рекомендует Webhook.

## 4. TLS и сертификаты

Актуальный API: `https://platform-api2.max.ru`.

Если в системном хранилище нет нужной цепочки, задайте путь к проверенному PEM
bundle:

```env
MAX_CA_BUNDLE=/etc/hermes/max-ca-bundle.pem
```

В bundle должны входить системные корни и актуальная доверенная цепочка,
необходимая MAX. Нельзя исправлять проблему через `verify=False`.

## 5. Webhook

```env
MAX_WEBHOOK_URL=https://example.ru/hermes/max
MAX_WEBHOOK_SECRET=<5-256 символов: латиница, цифры, _ или ->
```

MAX требует HTTPS на порту 443, доверенный сертификат и HTTP 200 не позднее
30 секунд. Плагин предоставляет `MaxAdapter.handle_webhook()`, проверку
`X-Max-Bot-Api-Secret`, bounded queue и дедупликацию. В текущем MVP публичный
HTTP listener внутри Hermes не запускается: нужен отдельный ASGI/reverse-proxy
ingress, который передаст запрос в этот метод. В репозитории есть готовый
отдельный процесс:

```powershell
python -m pip install -e ".[webhook]"
python scripts/max_webhook_server.py
```

`MAX_INBOX_PATH` должен указывать на один и тот же SQLite-файл у ingress и
Hermes. Публичный HTTPS/443 и TLS termination остаются ответственностью
reverse proxy. Подробности: `deployment runbook` и
`TLS deployment notes`.

## 6. Политика доступа

По умолчанию доступ закрыт:

- `MAX_ALLOWED_USERS` — разрешённые DM и группы;
- `MAX_GROUP_ALLOWED_USERS` — пользователи только для групп;
- `MAX_GROUP_ALLOWED_CHATS` — разрешённые group chat ID;
- `MAX_ALLOW_ALL_USERS=true` — только временная разработческая настройка.

Важно: текущий глобальный Hermes registry сначала применяет
`MAX_ALLOWED_USERS`. Поэтому пользователи, которым разрешён доступ только в
группе, всё равно должны присутствовать в глобальном allowlist; групповые
переменные дополнительно сужают решение внутри MAX-плагина, а не обходят
глобальную проверку Hermes.

Не включайте `MAX_ALLOW_ALL_USERS` для публичного бота.

## 7. Что сейчас реализовано

- текстовые DM и базовый group routing;
- нормализация `body.mid` и `recipient.chat_type`;
- chunking до 4000 символов;
- `Authorization` и API v2;
- Long Polling с marker;
- Webhook secret, ACK decision, bounded queue и dedup;
- Hermes plugin contract, YAML hook и standalone sender;
- входящие image/audio/video/file через локальный Hermes media cache;
- исходящие `MEDIA:` через актуальный `/uploads` и `payload.token`;
- ограничение размера и проверка официальных HTTPS media-hosts;
- безопасная TLS-политика.

Пока не считаются release-ready без отдельной проверки на disposable bot:
живой media acceptance, полноценный streaming UX и полевой тест без VPN.

## 8. Проверка и откат

```bash
python -m pytest -q
```

Для live-проверки задайте `MAX_BOT_TOKEN` и `MAX_CA_BUNDLE` только в окружении
процесса и запустите `scripts/max_live_smoke.py`. Проверка
`scripts/max_adapter_live_smoke.py` использует временный SQLite и collector
вместо вызова модели Hermes; активный gateway не запускается и не
перезапускается.

Перед рестартом gateway выполните loader smoke-тест на текущей версии Hermes.
Для отката остановите gateway и удалите или перенаправьте только
`~/.hermes/plugins/max`. Файлы Hermes core при установке плагина не меняются.
