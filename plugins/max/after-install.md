# MAX установлен

Плагин MAX установлен в пользовательский каталог Hermes и включён, если была
использована команда `--enable`.

Следующие шаги:

1. Если токен ещё не сохранён, создайте бота в MAX for Business и задайте
   `MAX_BOT_TOKEN` через защищённый prompt Hermes или локальный `.env`.
2. Для разработки выберите Long Polling. Для постоянного Webhook нужен
   публичный HTTPS URL на порту 443 и `MAX_WEBHOOK_URL`.
3. Укажите `MAX_ALLOWED_USERS`; не включайте `MAX_ALLOW_ALL_USERS` на публичном
   боте без отдельного решения владельца.
4. Проверьте `hermes plugins list --user`, `hermes doctor`, затем выполните
   `hermes gateway restart` и отправьте тестовое сообщение.

Полная русская инструкция: [настройка MAX](https://github.com/dimasalmin/hermes-agent-ru-messengers/blob/main/docs/ru/max-setup.md).

## English summary

Create the MAX bot, store `MAX_BOT_TOKEN` securely, choose Long Polling or a
public HTTPS Webhook, keep an allowlist, run `hermes doctor`, restart the gateway,
and test a real message.
