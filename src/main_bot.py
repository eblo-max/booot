import asyncio

import structlog
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from src.bot.handlers import common, company, new_search, searches
from src.config import settings
from src.logging_conf import setup_logging

log = structlog.get_logger()

COMMANDS = [
    BotCommand(command="new_search", description="Создать поисковый запрос"),
    BotCommand(command="searches", description="Мои запросы"),
    BotCommand(command="run_search", description="Запустить запрос"),
    BotCommand(command="company", description="Карточка компании по ИНН"),
    BotCommand(command="favorites", description="Избранное"),
    BotCommand(command="upload", description="Импорт Excel/CSV"),
    BotCommand(command="export", description="Выгрузка в Excel"),
    BotCommand(command="status", description="Состояние источников"),
    BotCommand(command="help", description="Справка"),
]


async def main() -> None:
    setup_logging()
    print("[bot] процесс запущен, инициализирую aiogram", flush=True)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    # порядок важен: common первым, wizard последним — он ловит свободный текст
    dp.include_router(common.router)
    dp.include_router(searches.router)
    dp.include_router(company.router)
    dp.include_router(new_search.router)

    await bot.set_my_commands(COMMANDS)
    me = await bot.get_me()
    log.info("bot_started", username=me.username, provider=settings.primary_provider)

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
        # сюда попадаем только при штатной остановке polling — на Railway это аномалия
        log.warning("polling_stopped_unexpectedly")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        # без этого падение до настройки логов уходит в пустоту
        import traceback

        traceback.print_exc()
        raise
