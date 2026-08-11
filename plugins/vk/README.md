# Плагин VK для Hermes Agent

Внешний plugin подключает Hermes Agent к сообществу VK через Community Long
Poll. Ядро Hermes не изменяется, поэтому plugin можно обновлять отдельно от
релизов Hermes.

Установка из ссылки на репозиторий:

```bash
hermes plugins install dimasalmin/hermes-agent-ru-messengers/plugins/vk --enable
```

Реализованы прямой HTTP-транспорт VK и Community Long Poll, устойчивое
состояние и дедупликация в SQLite, маршрутизация DM/групп через allowlist,
разбиение сообщений, редактирование, typing, pairing, callback-клавиатуры и
ограниченный транспорт фото, голосовых сообщений и документов.

Команды оператора доступны как `hermes vk ...`, если установленная версия
Hermes предоставляет CLI hook. Pairing-коды хранятся в SQLite только в виде
хеша.

## Обязательные переменные

```env
VK_GROUP_TOKEN=<community access token>
VK_GROUP_ID=<numeric community id>
VK_ALLOWED_USERS=<comma-separated VK user ids>
```

Полная русская инструкция: [настройка VK](https://github.com/dimasalmin/hermes-agent-ru-messengers/blob/main/docs/ru/vk-setup.md).

## English summary

External VK Community Long Poll plugin with allowlists, SQLite state,
callbacks, pairing and bounded media transport. Install only the `plugins/vk`
subdirectory, keep tokens out of chat history, and use the
[English setup guide](https://github.com/dimasalmin/hermes-agent-ru-messengers/blob/main/docs/en/vk-setup.md).
