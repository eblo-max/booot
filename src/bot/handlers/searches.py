import structlog
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from src.bot.keyboards.company import confirm_delete_kb, query_kb
from src.db import repositories as repo
from src.db.base import session_scope
from src.providers.registry import build_primary_provider
from src.services import delivery
from src.services.search_runner import SearchRunner

log = structlog.get_logger()
router = Router()

_SCHEDULE_LABEL = {"daily": "ежедневно", "weekly": "раз в неделю", "manual": "вручную"}


@router.message(Command("searches"))
async def cmd_searches(message: Message) -> None:
    await _render_list(message.answer, message.from_user.id)


@router.callback_query(F.data == "q:list")
async def cb_list(callback: CallbackQuery) -> None:
    await _render_list(callback.message.answer, callback.from_user.id)
    await callback.answer()


async def _render_list(send, telegram_id: int) -> None:
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, telegram_id, None)
        queries = await repo.list_queries(session, user.id)
        if not queries:
            await send("Сохранённых запросов пока нет. Создайте первый: /new_search")
            return

        for query in queries:
            total = await repo.count_results(session, query.id)
            mark = "🟢" if query.is_active else "⏸"
            last = query.last_run_at.strftime("%d.%m %H:%M") if query.last_run_at else "не запускался"
            text = (
                f"{mark} <b>{query.name}</b>\n"
                f"Проверка: {_SCHEDULE_LABEL.get(query.schedule, query.schedule)}\n"
                f"Последний запуск: {last}\n"
                f"Всего найдено: {total}"
            )
            await send(text, reply_markup=query_kb(query.id, query.is_active))


@router.callback_query(F.data.startswith("q:card:"))
async def cb_card(callback: CallbackQuery) -> None:
    query_id = int(callback.data.split(":")[2])
    async with session_scope() as session:
        query = await repo.get_query(session, query_id)
        if not query:
            await callback.answer("Запрос уже удалён", show_alert=True)
            return
        await callback.message.edit_reply_markup(reply_markup=query_kb(query.id, query.is_active))
    await callback.answer()


@router.message(Command("run_search"))
async def cmd_run_search(message: Message) -> None:
    await message.answer("Выберите запрос в /searches и нажмите «Запустить сейчас».")


@router.callback_query(F.data.startswith("q:run:"))
async def cb_run(callback: CallbackQuery, bot: Bot) -> None:
    query_id = int(callback.data.split(":")[2])
    await callback.answer("Запускаю…")
    await run_and_deliver(bot, callback.from_user.id, query_id)


async def run_and_deliver(bot: Bot, chat_id: int, query_id: int) -> None:
    """Общая точка входа: и кнопка, и планировщик идут сюда."""
    provider = build_primary_provider()
    runner = SearchRunner(provider)

    async with session_scope() as session:
        if not await repo.try_lock_query(session, query_id):
            await bot.send_message(chat_id, "Этот запрос уже выполняется, дождитесь окончания.")
            return

        try:
            query = await repo.get_query(session, query_id)
            if query is None:
                await bot.send_message(chat_id, "Запрос не найден.")
                return

            await bot.send_message(chat_id, f"🔍 Запускаю «{query.name}»…")
            outcome = await runner.run(session, query)
            await session.flush()
            await delivery.send_outcome(bot, chat_id, outcome, session)
        finally:
            await repo.unlock_query(session, query_id)
            await provider.close()


@router.callback_query(F.data.startswith("q:more:"))
async def cb_more(callback: CallbackQuery, bot: Bot) -> None:
    query_id = int(callback.data.split(":")[2])
    await callback.answer()
    async with session_scope() as session:
        query = await repo.get_query(session, query_id)
        if not query:
            return
        sent = await delivery.send_pending(bot, callback.from_user.id, query_id, query.name, session)
        left = await repo.count_unsent(session, query_id)

    if sent == 0:
        await bot.send_message(callback.from_user.id, "Больше новых компаний нет.")
    elif left:
        from src.bot.keyboards.company import more_results_kb

        await bot.send_message(
            callback.from_user.id, f"Осталось показать: {left}.", reply_markup=more_results_kb(query_id)
        )


@router.callback_query(F.data.startswith("q:pause:"))
async def cb_pause(callback: CallbackQuery) -> None:
    await _toggle(callback, active=False, note="приостановлен")


@router.callback_query(F.data.startswith("q:resume:"))
async def cb_resume(callback: CallbackQuery) -> None:
    await _toggle(callback, active=True, note="возобновлён")


async def _toggle(callback: CallbackQuery, active: bool, note: str) -> None:
    query_id = int(callback.data.split(":")[2])
    async with session_scope() as session:
        await repo.set_query_active(session, query_id, active)
    await callback.message.edit_reply_markup(reply_markup=query_kb(query_id, active))
    await callback.answer(f"Запрос {note}")


@router.callback_query(F.data.startswith("q:del:"))
async def cb_delete_ask(callback: CallbackQuery) -> None:
    query_id = int(callback.data.split(":")[2])
    await callback.message.edit_reply_markup(reply_markup=confirm_delete_kb(query_id))
    await callback.answer()


@router.callback_query(F.data.startswith("q:delyes:"))
async def cb_delete(callback: CallbackQuery) -> None:
    query_id = int(callback.data.split(":")[2])
    async with session_scope() as session:
        await repo.delete_query(session, query_id)
    await callback.message.edit_text("🗑 Запрос удалён вместе с историей результатов.")
    await callback.answer()


@router.callback_query(F.data.startswith("q:results:"))
async def cb_results(callback: CallbackQuery, bot: Bot) -> None:
    query_id = int(callback.data.split(":")[2])
    await callback.answer()
    async with session_scope() as session:
        query = await repo.get_query(session, query_id)
        rows = await repo.list_results(session, query_id, limit=delivery.BATCH_SIZE)
        if not rows:
            await bot.send_message(callback.from_user.id, "По этому запросу пока ничего не найдено.")
            return
        total = await repo.count_results(session, query_id)
        await bot.send_message(
            callback.from_user.id, f"Найдено всего: {total}. Показываю последние {len(rows)}."
        )
        from src.db.mapping import model_to_dto
        from src.services.search_runner import DeliveryItem, ItemKind

        items = [
            DeliveryItem(company=model_to_dto(r.company), result_id=r.id, kind=ItemKind.NEW)
            for r in rows
        ]
        await delivery.send_items(bot, callback.from_user.id, items, query.name, session)


@router.callback_query(F.data.startswith("q:export:"))
async def cb_export(callback: CallbackQuery) -> None:
    await callback.answer("Выгрузка в Excel — этап 3.", show_alert=True)
