# VK установлен

Плагин VK установлен в пользовательский каталог Hermes и включён, если была
использована команда `--enable`.

Следующие шаги владелец выполняет в VK:

1. Откройте или создайте сообщество и включите сообщения сообщества.
2. Включите Community Long Poll, создайте ключ доступа и задайте
   `VK_GROUP_TOKEN` через защищённый prompt Hermes или локальный `.env`.
3. Укажите числовой `VK_GROUP_ID` и `VK_ALLOWED_USERS`.
4. Проверьте `hermes plugins list --user`, `hermes doctor`, затем выполните
   `hermes gateway restart` и отправьте тестовое DM.

Не включайте `VK_ALLOW_ALL_USERS=true` на публичном боте без отдельного решения
владельца. Полная русская инструкция: [настройка VK](https://github.com/dimasalmin/hermes-agent-ru-messengers/blob/main/docs/ru/vk-setup.md).

## English summary

Enable community messages and Community Long Poll, store the token securely,
set the numeric group ID and allowlist, run `hermes doctor`, restart the gateway,
and test a real DM.
