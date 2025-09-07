# USDT=Dollar — автопостинг новостей в Telegram (бесплатно)

## Как запустить
1) Создайте бота в @BotFather → /newbot → токен.
2) Добавьте бота админом в канал (например, @USDT_Dollar).
3) Загрузите все файлы в репозиторий GitHub.
4) В репозитории: Settings → Secrets → Actions → добавьте:
   - BOT_TOKEN = токен из @BotFather
   - CHANNEL_ID = @имя_вашего_канала (например, @USDT_Dollar)
5) Вкладка Actions → включите и запустите вручную “Run workflow”.

## Настройка
- Источники: `bot/poster.py` → список RSS_FEEDS.
- Частота: `.github/workflows/post.yml` → cron.
- Карточка: функция draw_card() в poster.py.
