# Инструкция для AI-агента

Этот файл предназначен для Hermes Agent, Claude Code, Codex и других агентов,
которые получили ссылку на репозиторий и должны установить интеграцию для
пользователя. Основной язык — русский.

## Цель

Установить выбранный внешний platform plugin в пользовательский каталог Hermes,
не изменяя ядро Hermes и его управляемый release checkout. Затем провести
безопасную локальную проверку и довести владельца до ручных настроек MAX или VK.

## Что спросить до начала

Задавайте только вопросы, без которых нельзя продолжить:

1. Нужен MAX, VK или оба канала.
2. Для MAX: Webhook или Long Polling для разработки.
3. Какой пользовательский ID добавить в allowlist. Если ID ещё неизвестен,
   объясните, где его получить, и не включайте открытый режим.
4. Есть ли у владельца доступ к кабинету MAX/VK, DNS и TLS, если это требуется.

Не просите присылать токены в историю чата. Используйте защищённый prompt
`hermes plugins install`/локальный `.env` Hermes или подключённый secret manager.
Не выводите токены в логи, отчёты и команды, которые будут сохранены в истории.

## Установка

Это монорепозиторий с двумя самостоятельными каталогами. Устанавливайте
выбранный подкаталог через штатный Git-инсталлятор Hermes:

```bash
# MAX
hermes plugins install dimasalmin/hermes-agent-ru-messengers/plugins/max --enable

# VK
hermes plugins install dimasalmin/hermes-agent-ru-messengers/plugins/vk --enable
```

Полная URL-форма также поддерживается:

```text
https://github.com/dimasalmin/hermes-agent-ru-messengers/tree/main/plugins/max
https://github.com/dimasalmin/hermes-agent-ru-messengers/tree/main/plugins/vk
```

Не устанавливайте корень репозитория как один plugin и не копируйте файлы в
`gateway/` или в checkout Hermes. Если `httpx` отсутствует, сначала сообщите
пользователю о необходимой зависимости и запросите разрешение на её установку.

## Проверка после установки

Выполните и сохраните результат:

```bash
hermes plugins list --user
hermes doctor
```

Убедитесь, что выбранный манифест (`max-platform` или `vk`) виден и включён.
После изменения списка plugins перезапустите gateway:

```bash
hermes gateway restart
```

До live-проверки не заявляйте, что канал работает в production. Loader checks
и `hermes doctor` подтверждают установку и локальную готовность, но не заменяют
реальный обмен сообщениями.

## Что должен сделать человек на площадке

### MAX

- создать бота в MAX for Business и получить токен;
- выбрать Webhook или Long Polling;
- для Webhook настроить публичный HTTPS и порт 443;
- указать безопасный allowlist пользователей и отправить тестовое сообщение.

### VK

- открыть или создать сообщество;
- включить сообщения сообщества и Community Long Poll;
- создать ключ доступа с необходимыми правами и сообщить числовой ID сообщества;
- указать allowlist и отправить тестовое DM.

Открытые режимы `MAX_ALLOW_ALL_USERS=true` и `VK_ALLOW_ALL_USERS=true` не
включайте на публичном боте без отдельного решения владельца и проверки риска.

## Формат отчёта агенту

В конце сообщите отдельно:

- установлен ли plugin и включён ли он;
- прошли ли loader/doctor checks;
- какие секреты и ID настроены, не раскрывая их значения;
- какие действия владельца остались;
- прошёл ли реальный тест входящего и исходящего сообщения.

## English summary

Install only `plugins/max` or `plugins/vk` with `hermes plugins install ... --enable`.
Keep plugins outside Hermes core, protect tokens, verify `hermes plugins list`
and `hermes doctor`, and leave account, DNS/TLS, and live-message actions to the
owner when access is required.
