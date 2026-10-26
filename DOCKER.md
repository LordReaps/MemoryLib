# Запуск через Docker Compose

Нужны Docker Engine и плагин Docker Compose v2.

1. Скопируйте `.env.example` в `.env`.
2. Укажите уникальные `SECRET_KEY`, `SITE_PASSWORD`, `DB_PASSWORD`, токен бота и Telegram chat ID.
3. Запустите контейнеры:

   ```sh
   docker compose up -d --build
   ```

Compose запускает PostgreSQL, применяет миграции, затем стартуют сайт и Telegram-бот. Django доступен на порту `8000`; база данных доступна только контейнерам. `DB_HOST` внутри Compose автоматически устанавливается в `db`, менять его в `.env` для Docker не нужно.

Полезные команды:

```sh
docker compose ps
docker compose logs -f web telegram_bot
docker compose restart web telegram_bot
docker compose down
```

База и загруженные фотографии находятся в именованных Docker volumes `postgres_data` и `media`; обычная команда `docker compose down` их сохраняет. Не используйте `docker compose down -v`, если не хотите удалить эти данные.

Это начальная конфигурация без отдельного production web-сервера и TLS: Django запускается встроенной командой `runserver`, а порт `8000` публикуется напрямую. `--insecure` нужен для раздачи статики при `DEBUG=False`, но не превращает `runserver` в production-сервер. Не публикуйте такой вариант в открытый интернет без последующей настройки production-доступа и HTTPS.
