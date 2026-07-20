import structlog
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from src.db import repositories as repo
from src.db.base import session_scope
from src.db.mapping import model_to_dto
from src.domain.tax_status import TaxStatus, describe

log = structlog.get_logger()
router = Router()


@router.callback_query(F.data.startswith("co:fav:"))
async def cb_favorite(callback: CallbackQuery) -> None:
    result_id = int(callback.data.split(":")[2])
    async with session_scope() as session:
        row = await repo.get_result_by_id(session, result_id)
        if row is None:
            await callback.answer("Запись не найдена", show_alert=True)
            return
        new_value = not row.is_favorite
        await repo.set_result_flag(session, result_id, is_favorite=new_value)
    await callback.answer("⭐ Добавлено в избранное" if new_value else "Убрано из избранного")


@router.callback_query(F.data.startswith("co:hide:"))
async def cb_hide(callback: CallbackQuery) -> None:
    result_id = int(callback.data.split(":")[2])
    async with session_scope() as session:
        await repo.set_result_flag(session, result_id, is_hidden=True)
    await callback.answer("Больше не покажу эту компанию по этому запросу")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


@router.callback_query(F.data.startswith("co:info:"))
async def cb_info(callback: CallbackQuery) -> None:
    result_id = int(callback.data.split(":")[2])
    async with session_scope() as session:
        row = await repo.get_result_by_id(session, result_id)
        if row is None:
            await callback.answer("Запись не найдена", show_alert=True)
            return
        c = model_to_dto(row.company)
        first_seen = row.first_seen_at.strftime("%d.%m.%Y %H:%M")

    lines = [
        f"<b>{c.name}</b>",
        f"Полное наименование: {c.full_name or '—'}",
        f"Руководитель: {c.manager_name or 'нет данных'}",
        f"Все ОКВЭД: {', '.join(c.okved_list) if c.okved_list else 'нет данных'}",
        f"Налоговый режим: {describe(c.tax_status, c.tax_regimes, c.tax_source)}",
    ]
    if c.tax_status == TaxStatus.OSNO_PROBABLE:
        lines.append(
            "<i>Спецрежимы не найдены в проверенном источнике. Это не равно официальному "
            "подтверждению ОСНО — уточняйте у компании.</i>"
        )
    lines.append(f"Впервые найдена: {first_seen}")
    lines.append(f"Источник: {c.source}")

    await callback.message.answer("\n".join(lines), disable_web_page_preview=True)
    await callback.answer()


@router.callback_query(F.data.startswith("co:recheck:"))
async def cb_recheck(callback: CallbackQuery) -> None:
    result_id = int(callback.data.split(":")[2])
    from src.providers.registry import build_primary_provider

    provider = build_primary_provider()
    try:
        async with session_scope() as session:
            row = await repo.get_result_by_id(session, result_id)
            if row is None:
                await callback.answer("Запись не найдена", show_alert=True)
                return
            key = row.company.ogrn or row.company.inn
            fresh = await provider.get_company(key) if key else None
            if fresh is None:
                await callback.answer("Источник не вернул данные по этой компании", show_alert=True)
                return
            await repo.upsert_company(session, fresh)
    finally:
        await provider.close()

    await callback.answer("Данные обновлены")


@router.message(Command("company"))
async def cmd_company(message: Message) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Укажите ИНН или ОГРН: <code>/company 7707083893</code>")
        return

    from src.providers.registry import build_primary_provider

    provider = build_primary_provider()
    try:
        company = await provider.get_company(parts[1].strip())
    finally:
        await provider.close()

    if company is None:
        await message.answer("Компания не найдена в подключённом источнике.")
        return

    from src.services.delivery import format_card
    from src.services.search_runner import ItemKind

    await message.answer(format_card(company, "разовый запрос", ItemKind.NEW))


@router.message(Command("favorites"))
async def cmd_favorites(message: Message) -> None:
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.from_user.id, message.from_user.username)
        queries = await repo.list_queries(session, user.id)
        found = []
        for query in queries:
            rows = await repo.list_results(session, query.id, only_favorites=True, limit=20)
            found.extend((query.name, row) for row in rows)

    if not found:
        await message.answer("В избранном пока пусто.")
        return

    lines = ["<b>Избранные компании</b>", ""]
    for query_name, row in found[:50]:
        lines.append(f"• {row.company.name} — ИНН {row.company.inn or '—'} <i>({query_name})</i>")
    await message.answer("\n".join(lines))
