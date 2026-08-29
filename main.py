"""
Bot entrypoint. Uses long polling (no public endpoint needed — ideal for a
single always-on container such as Azure Container Instances).
"""

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

import config
from handlers import admin, commands, report
from utils import sharepoint, single_instance

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("bot")


async def main() -> None:
    config.validate()

    # Hard single-instance guarantee: if another bot process already holds the
    # OS lock, refuse to start so two pollers can never conflict.
    try:
        single_instance.acquire(os.getenv("LOCK_PATH", "./.bot.lock"))
    except single_instance.AlreadyRunning as exc:
        logger.error("%s Refusing to start a second instance.", exc)
        return

    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(commands.router)
    dp.include_router(admin.router)
    dp.include_router(report.router)

    # If SharePoint is configured, pull the current workbook so we append to it
    # instead of overwriting it after a restart on ephemeral storage.
    if sharepoint.is_configured():
        await sharepoint.sync_from_remote(config.EXCEL_PATH)

    # Drop any updates accumulated while the bot was offline.
    await bot.delete_webhook(drop_pending_updates=True)

    logger.info("Bot started (long polling).")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        logger.info("Bot stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
