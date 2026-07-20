"""Формирование и отправка результатов в Telegram.

Сначала сводка, потом порции по BATCH_SIZE карточек. Сотни отдельных уведомлений
одновременно не отправляются никогда.
"""

import asyncio
import html
from datetime import datetime

import structlog
from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter

from src.db import repositories as repo
from src.db.mapping import model_to_dto
from src.db.models import SearchResult
from src.domain.company import CompanyDTO
from src.domain.tax_status import describe
from src.services.search_runner import DeliveryItem, ItemKind, RunOutcome

log = structlog.get_logger()

BATCH_SIZE = 10
SEND_DELAY = 1.0  # сек между сообщениями в один чат


def _money(value) -> str:
    if value is None:
        return "нет данных"
    return f"{value:,.0f}".replace(",", " ") + " ₽"


def _esc(value) -> str:
    return html.escape(str(value)) if value is not None else ""


def format_summary(outcome: RunOutcome) -> str:
    lines = [
        f"<b>Запрос:</b> «{_esc(outcome.query_name)}»",
        f"Проверено: {outcome.received:,}".replace(",", " ") + " компаний",
        f"Соответствуют критериям: {outcome.matched}",
        f"Уже показывались ранее: {outcome.already_seen}",
        f"Новых компаний: {outcome.new}",
    ]
    if outcome.changed:
        lines.append(f"Изменились с прошлого раза: {outcome.changed}")
    lines += [
        f"С контактами: {outcome.with_contacts}",
        f"Вероятная ОСНО: {outcome.probable_osno}",
    ]
    if outcome.unknown_tax:
        lines.append(f"Налоговый режим неизвестен: {outcome.unknown_tax}")
    return "\n".join(lines)


def format_card(c: CompanyDTO, query_name: str, kind: ItemKind, reasons: list[str] | None = None) -> str:
    if kind is ItemKind.CHANGED:
        head = f"🔄 <b>Изменения по запросу</b> «{_esc(query_name)}»"
    else:
        head = f"🆕 <b>Новая компания по запросу</b> «{_esc(query_name)}»"

    lines = [head, "", f"<b>{_esc(c.name)}</b>"]
    lines.append(f"ИНН: {_esc(c.inn) or 'нет данных'}")
    lines.append(f"ОГРН: {_esc(c.ogrn) or 'нет данных'}")
    if c.region_name or c.region_code:
        lines.append(f"Регион: {_esc(c.region_name or c.region_code)}")
    if c.registration_date:
        lines.append(f"Дата регистрации: {c.registration_date.strftime('%d.%m.%Y')}")
    if c.main_okved:
        lines.append(f"Основной ОКВЭД: {_esc(c.main_okved)}")

    year = f" за {c.revenue_year}" if c.revenue_year else ""
    lines.append(f"Выручка{year}: {_money(c.revenue)}")
    if c.profit is not None:
        lines.append(f"Чистая прибыль: {_money(c.profit)}")

    lines.append(f"Налоговый режим: {describe(c.tax_status, c.tax_regimes, c.tax_source)}")

    if c.phones:
        lines.append(f"Телефон: {_esc(', '.join(c.phones))}")
    if c.emails:
        lines.append(f"E-mail: {_esc(', '.join(c.emails))}")
    if c.website:
        lines.append(f"Сайт: {_esc(c.website)}")

    lines.append(f"Источник: {_esc(c.source)}")
    checked = c.source_updated_at or datetime.now()
    lines.append(f"Данные проверены: {checked.strftime('%d.%m.%Y')}")

    if kind is ItemKind.CHANGED and reasons:
        lines.append("")
        lines.append("<i>Что изменилось: " + _esc(", ".join(reasons)) + "</i>")

    return "\n".join(lines)


async def _safe_send(bot: Bot, chat_id: int, text: str, reply_markup=None) -> None:
    """Уважает 429 от Telegram."""
    try:
        await bot.send_message(chat_id, text, reply_markup=reply_markup, disable_web_page_preview=True)
    except TelegramRetryAfter as exc:
        log.warning("telegram_rate_limited", retry_after=exc.retry_after)
        await asyncio.sleep(exc.retry_after + 1)
        await bot.send_message(chat_id, text, reply_markup=reply_markup, disable_web_page_preview=True)


async def send_outcome(bot: Bot, chat_id: int, outcome: RunOutcome, session) -> None:
    """Сводка + первая порция карточек."""
    from src.bot.keyboards.company import more_results_kb

    if not outcome.ok:
        text = f"⚠️ Запрос «{_esc(outcome.query_name)}» завершился с ошибкой.\n\n{_esc(outcome.error)}"
        if outcome.notice:
            text += f"\n\n{_esc(outcome.notice)}"
        await _safe_send(bot, chat_id, text)
        return

    await _safe_send(bot, chat_id, format_summary(outcome))

    if not outcome.items:
        await _safe_send(bot, chat_id, "Новых компаний по этому запросу нет.")
        return

    sent = await send_items(bot, chat_id, outcome.items, outcome.query_name, session)

    remaining = len(outcome.items) - sent
    if remaining > 0:
        await _safe_send(
            bot,
            chat_id,
            f"Показано {sent} из {len(outcome.items)}. Осталось: {remaining}.",
            reply_markup=more_results_kb(outcome.query_id),
        )


async def send_items(
    bot: Bot, chat_id: int, items: list[DeliveryItem], query_name: str, session
) -> int:
    """Отправляет не больше BATCH_SIZE карточек. Возвращает количество отправленных."""
    from src.bot.keyboards.company import company_kb

    batch = items[:BATCH_SIZE]
    for item in batch:
        await _safe_send(
            bot,
            chat_id,
            format_card(item.company, query_name, item.kind, item.reasons),
            reply_markup=company_kb(item.result_id),
        )
        await asyncio.sleep(SEND_DELAY)

    await repo.mark_sent(session, [i.result_id for i in batch])
    return len(batch)


async def send_pending(bot: Bot, chat_id: int, query_id: int, query_name: str, session) -> int:
    """Досылает следующую порцию из ещё не отправленных результатов."""
    rows: list[SearchResult] = await repo.list_unsent(session, query_id, BATCH_SIZE)
    items = [
        DeliveryItem(
            company=model_to_dto(row.company),
            result_id=row.id,
            kind=ItemKind.CHANGED if row.change_reason else ItemKind.NEW,
            reasons=list(row.change_reason or []),
        )
        for row in rows
    ]
    if not items:
        return 0
    return await send_items(bot, chat_id, items, query_name, session)
