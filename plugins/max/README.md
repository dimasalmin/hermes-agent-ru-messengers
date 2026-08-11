# Плагин MAX для Hermes Agent

Каталог самодостаточен: Hermes загружает внешний plugin как отдельный
`hermes_plugins.<slug>`. Устанавливайте его штатной командой:

```bash
hermes plugins install dimasalmin/hermes-agent-ru-messengers/plugins/max --enable
```

Не помещайте plugin в исходное дерево Hermes. Плагин работает с MAX Bot API v2,
поддерживает Long Polling для разработки, заготовки Webhook-приёмника, allowlist
и проверку TLS. Токен MAX храните в защищённом prompt Hermes или локальном
`.env`, не в истории чата.

Полная русская инструкция: [настройка MAX](https://github.com/dimasalmin/hermes-agent-ru-messengers/blob/main/docs/ru/max-setup.md).

## English summary

Self-contained MAX Bot API v2 platform plugin. Install the `plugins/max`
subdirectory with Hermes, keep it outside Hermes core, protect the bot token,
and follow the [English setup guide](https://github.com/dimasalmin/hermes-agent-ru-messengers/blob/main/docs/en/max-setup.md).
