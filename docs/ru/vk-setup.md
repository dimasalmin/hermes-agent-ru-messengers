# Настройка VK-адаптера Hermes Agent

## 1. Создать токен сообщества

1. Создайте сообщество VK или выберите существующее.
2. Включите сообщения сообщества.
3. В разделе API включите Community Long Poll.
4. Создайте ключ доступа сообщества с правом отправки сообщений. Для медиа
   добавьте права на фотографии и документы.
5. Запишите числовой ID сообщества из URL `vk.com/club<ID>`.

В этой русской инструкции уже собраны необходимые действия для настройки.
Отдельная англоязычная страница VK не требуется.

Справочные методы API:

- [messages.send](https://dev.vk.com/method/messages.send)
- [messages.sendMessageEventAnswer](https://dev.vk.com/method/messages.sendMessageEventAnswer)
- [photos.getMessagesUploadServer](https://dev.vk.com/method/photos.getMessagesUploadServer)
- [docs.getMessagesUploadServer](https://dev.vk.com/method/docs.getMessagesUploadServer)

## 2. Установить внешний plugin

```bash
pip install -e ".[vk]"
ln -s "$(pwd)/plugins/vk" ~/.hermes/plugins/vk
```

Репозиторий должен оставаться отдельным от checkout Hermes. Не копируйте
файлы в `gateway/` и в управляемый каталог release Hermes.

## 3. Настроить

```env
VK_GROUP_TOKEN=vk1.a...
VK_GROUP_ID=123456789
VK_ALLOWED_USERS=100000001
VK_STATE_PATH=/home/user/.hermes/vk/state.sqlite3
VK_DM_POLICY=allowlist
VK_GROUP_POLICY=allowlist
VK_REQUIRE_MENTION=true
```

Запустите Hermes обычным способом. Plugin получает сервер Long Poll, сохраняет
`server/key/ts` в SQLite и восстанавливает polling после временного сбоя.
Просроченный ключ или marker приводит к новой инициализации через
`groups.getLongPollServer`.

Политика DM и чатов по умолчанию `allowlist`. Для pairing-режима:

```env
VK_DM_POLICY=pairing
```

Оператор выдаёт одноразовый код и передаёт его пользователю:

```bash
hermes vk pairing issue --user-id 100000001
```

Пользователь подтверждает подключение командой `/pair <code>`. Код хранится в
SQLite только в виде хеша и не попадает в логи. Значение `open` требует
явного решения оператора и не должно использоваться на непроверенном
публичном боте.

## 4. DM, группы и безопасность

- В DM `peer_id == user_id`.
- В беседе `peer_id = 2_000_000_000 + chat_id`.
- Для групп по умолчанию нужны allowlist и явное упоминание сообщества.
- `VK_GROUP_ALLOWED_CHATS` содержит именно `peer_id`, а не `chat_id`.
- `VK_ALLOW_ALL_USERS=true` не следует использовать на открытом боте без
  отдельного security review.
- Callback payload непрозрачный, одноразовый, ограничен временем жизни и
  связан с VK user/peer. Нажатие чужой кнопки не выполняет действие.

## 5. Медиа и диагностика

Входящие фото, голосовые сообщения и документы скачиваются только по HTTPS с
проверкой host и ограничением размера `VK_MEDIA_MAX_BYTES`, после чего передаются
в media cache Hermes. Исходящие `MEDIA:/path` загружаются через VK photo/doc
upload API.

Проверка:

```bash
python -m pytest -q
hermes doctor
hermes vk validate
hermes vk validate --live
```

Если бот молчит, проверьте права токена, сообщения сообщества, Long Poll,
`VK_GROUP_ID`, `VK_ALLOWED_USERS` и журнал загрузки plugin. Live-проверка требует
отдельного disposable token и реального тестового VK user.
