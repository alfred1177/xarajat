"""Точка входа: запускает веб-сервер Mini App и бота в одном процессе.

    python -m app.main
"""
import asyncio
import logging

import uvicorn
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from .api import create_app
from .bot import TelegramNotifier, build_dispatcher, setup_menu_button
from .config import load_settings
from .db import DB


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = load_settings()
    if not s.bot_token:
        raise SystemExit("Не задан BOT_TOKEN (см. .env.example)")
    if not s.owner_ids:
        raise SystemExit("Не задан OWNER_IDS — ваш Telegram ID (узнать: @userinfobot)")
    if s.dev_mode:
        logging.warning("DEV_MODE=1: проверка подписи Telegram ОТКЛЮЧЕНА. Не используйте на сервере!")

    db = DB(s.db_path)
    bot = Bot(s.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    app = create_app(db, s, TelegramNotifier(bot, db, s))
    dp = build_dispatcher(db, s)

    await setup_menu_button(bot, s)
    server = uvicorn.Server(uvicorn.Config(app, host=s.host, port=s.port, log_level="info"))
    polling = asyncio.create_task(dp.start_polling(bot, handle_signals=False))
    try:
        await server.serve()  # uvicorn сам ловит Ctrl+C / SIGTERM
    finally:
        await dp.stop_polling()
        await polling
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
