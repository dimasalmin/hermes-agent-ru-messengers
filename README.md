# Hermes Agent — плагины MAX и VK

[Русская версия](#русская-версия) · [English summary](#english-summary)

## Русская версия

Неофициальные внешние плагины платформы для [Hermes Agent](https://github.com/NousResearch/hermes-agent):

- MAX Bot API v2 через HTTPS Webhook или Long Polling для разработки;
- VK Community Long Poll с прямым HTTP-транспортом.

Плагины находятся вне ядра Hermes Agent, поэтому Hermes можно обновлять и откатывать независимо от этих интеграций.

Проект экспериментальный. Локальная проверка включает 133 детерминированных теста и loader checks. Рабочая проверка MAX/VK всё ещё требует одноразовых учётных данных и реального тестового пользователя или сообщества.

Проект не гарантирует доступность MAX/VK, прохождение whitelist-политик или сетевую доступность у конкретного оператора. Это нужно отдельно проверять для региона, оператора, устройства и режима сбоя.

### Что входит

- MAX Bot API v2, текстовая маршрутизация, Webhook/Long Polling, callback-кнопки, выбор модели, ограниченный транспорт медиа и проверка TLS;
- VK Community Long Poll, устойчивый marker и дедупликация, прямой API-клиент, callbacks, typing, редактирование сообщений, pairing, allowlist и ограниченный транспорт медиа;
- манифесты `plugin.yaml`, адаптеры платформ, standalone sender hooks, contract tests и loader smoke scripts.

### Установка для агента

Если вы передаёте этот репозиторий Hermes Agent или другому AI-агенту,
попросите установить только нужный канал. Репозиторий содержит два отдельных
плагина, поэтому корень репозитория не является устанавливаемым плагином.

Для MAX:

```bash
hermes plugins install dimasalmin/hermes-agent-ru-messengers/plugins/max --enable
```

Для VK:

```bash
hermes plugins install dimasalmin/hermes-agent-ru-messengers/plugins/vk --enable
```

Агент должен проверить `hermes plugins list`, выполнить `hermes doctor`,
показать, какой плагин установлен и включён, а затем перезапустить gateway:

```bash
hermes gateway restart
```

До установки агент спрашивает только критически важное: MAX или VK, режим
MAX (Webhook или Long Polling), идентификаторы для allowlist и готовность
владельца выполнить действия на стороне площадки. Токены вводятся в локальный
защищённый prompt Hermes или secret manager, а не в историю чата.

Человек всё равно должен создать бота или сообщество, выдать токен, включить
сообщения и Long Poll на стороне площадки, а для MAX Webhook — обеспечить
публичный HTTPS на порту 443. Агент может провести эти шаги по инструкции,
но не может выполнить их без доступа владельца к кабинетам MAX/VK и DNS/TLS.

Подробный алгоритм для агента находится в [AGENTS.md](AGENTS.md).

### Установка и настройка

Копируйте или подключайте только каталоги плагинов в пользовательский каталог Hermes. Не копируйте их в исходное дерево Hermes.

- [настройка MAX](docs/ru/max-setup.md);
- [настройка VK](docs/ru/vk-setup.md);
- [интерактивные сценарии MAX](docs/ru/max-interactive.md);
- [зачем Hermes нужны российские мессенджеры](docs/ru/why-russian-messengers.md).

После изменения Hermes запускайте loader smoke test до перезапуска production gateway. Third-party plugins в Hermes включаются явно:

```bash
hermes plugins list
hermes plugins enable max-platform
hermes plugins enable vk
```

### Разработка

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python -m compileall -q plugins tests scripts
```

Секреты для live-проверок передавайте только через окружение. Не публикуйте токены, cookies, базы данных, логи или production-конфигурацию.

### Конфигурация

MAX:

```env
MAX_BOT_TOKEN=<токен MAX for Business>
MAX_ALLOWED_USERS=<числовые ID пользователей через запятую>
```

VK:

```env
VK_GROUP_TOKEN=<токен сообщества>
VK_GROUP_ID=<числовой ID сообщества VK>
VK_ALLOWED_USERS=<числовые ID пользователей через запятую>
```

Оба адаптера по умолчанию используют ограничительный allowlist. Изучите полные параметры в инструкциях настройки.

### Безопасность и лицензия

Адаптеры обрабатывают внешние сетевые данные, вложения, callbacks и API-учётные данные. В реализации есть allowlists, ограничение размера медиа, redaction токенов, проверки HTTPS-хостов, привязка и срок действия callbacks, а также проверка TLS. Эти меры не заменяют проверку конкретного развёртывания.

Сообщайте о проблемах безопасности приватно по правилам [SECURITY.md](SECURITY.md). Лицензия — [MIT](LICENSE).

## English summary

Unofficial external MAX and VK platform plugins for Hermes Agent.

The project is experimental: 133 deterministic tests pass locally, while live MAX/VK acceptance and regional connectivity remain deployment-specific.

See the [English MAX setup](docs/en/max-setup.md), [English VK setup](docs/en/vk-setup.md), and [English interactive MAX guide](docs/en/max-interactive.md).

Keep the plugins outside the Hermes core checkout, enable only reviewed platforms, and never commit credentials or production data. Licensed under [MIT](LICENSE).
