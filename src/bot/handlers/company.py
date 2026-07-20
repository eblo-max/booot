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
    from src.providers.registry import build_lookup_provider

    provider = build_lookup_provider()
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


def _format_research(company_name: str, result) -> str:
    if not result.ok:
        return f"⚠️ Поиск по «{company_name}» не удался.\n\n{result.error}"

    lines = [f"🔍 <b>Веб-разведка: {company_name}</b>", ""]

    if result.uncertain:
        lines += [
            "⚠️ <i>Модель не уверена, что нашла именно эту компанию — "
            "сверьте название и сайт перед использованием.</i>",
            "",
        ]

    if result.emails:
        lines.append("<b>Почты со страниц компании</b>")
        for contact in result.emails:
            mark = " ✅" if contact.domain_match else ""
            note = f" — {contact.note}" if contact.note else ""
            lines.append(f"• <code>{contact.value}</code>{mark}{note}")
            if contact.source_url:
                lines.append(f"  <i>{contact.source_url}</i>")
        lines.append("")
    else:
        lines += ["Почт на открытых страницах не нашлось.", ""]

    if result.phones:
        lines.append("<b>Телефоны</b>")
        lines += [f"• {c.value}" for c in result.phones]
        lines.append("")

    if result.website:
        lines.append(f"Сайт: {result.website}")
    if result.activity:
        lines += ["", f"<b>Чем занимается</b>\n{result.activity}"]
    if result.signals:
        lines += ["", "<b>Замечено</b>"] + [f"• {s}" for s in result.signals]

    if result.discarded:
        lines += [
            "",
            f"<i>Отброшено как непроверенное: {len(result.discarded)} шт. "
            "Этих контактов не было ни на одной открытой странице.</i>",
        ]

    lines += [
        "",
        f"<i>✅ — домен почты совпадает с сайтом. "
        f"Поисков: {result.searches}, страниц открыто: {result.fetches}.</i>",
    ]
    return "\n".join(lines)


@router.callback_query(F.data.startswith("co:web:"))
async def cb_web_research(callback: CallbackQuery, bot) -> None:
    from src.config import settings

    if not settings.anthropic_api_key:
        await callback.answer(
            "Веб-разведка выключена: не задан ANTHROPIC_API_KEY.", show_alert=True
        )
        return

    result_id = int(callback.data.split(":")[2])
    await callback.answer("Ищу в вебе, это займёт до минуты…")

    async with session_scope() as session:
        row = await repo.get_result_by_id(session, result_id)
        if row is None:
            await bot.send_message(callback.from_user.id, "Запись не найдена.")
            return
        dto = model_to_dto(row.company)
        name = row.company.name

    research = await _run_research(dto)
    await bot.send_message(
        callback.from_user.id, _format_research(name, research), disable_web_page_preview=True
    )


async def _run_research(dto):
    from anthropic import AsyncAnthropic

    from src.config import settings
    from src.services.web_research import WebResearcher

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    try:
        return await WebResearcher(client, model=settings.research_model).research(dto)
    finally:
        await client.close()


@router.message(Command("deep"))
async def cmd_deep(message: Message) -> None:
    from src.config import settings

    if not settings.anthropic_api_key:
        await message.answer("Веб-разведка выключена: не задан ANTHROPIC_API_KEY.")
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Укажите ИНН: <code>/deep 7707083893</code>")
        return

    from src.providers.registry import build_lookup_provider

    provider = build_lookup_provider()
    try:
        company = await provider.get_company(parts[1].strip())
    finally:
        await provider.close()

    if company is None:
        await message.answer("Компания не найдена в подключённом источнике.")
        return

    await message.answer(f"🔍 Ищу в вебе «{company.name}», это займёт до минуты…")
    research = await _run_research(company)
    await message.answer(
        _format_research(company.name, research), disable_web_page_preview=True
    )


@router.message(Command("company"))
async def cmd_company(message: Message) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Укажите ИНН или ОГРН: <code>/company 7707083893</code>")
        return

    from src.providers.registry import build_lookup_provider

    provider = build_lookup_provider()
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
