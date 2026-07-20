import structlog
from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from src.config import settings
from src.db import repositories as repo
from src.db.base import session_scope
from src.providers.registry import build_primary_provider

log = structlog.get_logger()
router = Router()

HELP = """<b>Поиск юридических лиц</b>

/new_search — создать поисковый запрос
/searches — мои запросы и управление ими
/run_search — запустить запрос вручную
/company ИНН — карточка одной компании
/favorites — избранное
/status — состояние источников данных
/help — эта справка

Скоро: /upload (импорт Excel), /export (выгрузка), /pause_search.

<b>О налоговых режимах.</b> Бот никогда не пишет «ОСНО подтверждена» без источника.
Если спецрежимы проверены и не найдены — «вероятная ОСНО».
Если данных нет — «налоговый режим неизвестен»."""


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    async with session_scope() as session:
        user = await repo.get_or_create_user(
            session, message.from_user.id, message.from_user.username
        )
        if message.from_user.id in settings.admin_ids and user.role != "admin":
            user.role = "admin"

    await message.answer(
        "Привет. Я ищу российские юрлица по заданным критериям и присылаю только новые компании.\n\n"
        "Начните с /new_search — задам 13 вопросов и сохраню запрос.\n"
        "Полный список команд: /help"
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP)


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    provider = build_primary_provider()
    try:
        status = await provider.check_connection()
    except Exception as exc:  # noqa: BLE001 — статус не должен ронять бота
        status = None
        error = str(exc)
    finally:
        await provider.close()

    caps = provider.capabilities
    lines = [f"<b>Источник:</b> {provider.name}"]
    if status is None:
        lines.append(f"Состояние: ❌ ошибка — {error}")
    else:
        lines.append(f"Состояние: {'✅' if status.ok else '❌'} {status.message}")
        if status.quota_left is not None:
            lines.append(f"Остаток лимита: {status.quota_left}")

    lines += [
        "",
        f"Массовый поиск по фильтрам: {'да' if caps.mass_search else 'нет'}",
        f"Финансы: {'да' if caps.has_financials else 'нет'}",
        f"Контакты: {'да' if caps.has_contacts else 'нет'}",
        f"Налоговый режим: {'да' if caps.has_tax_regime else 'нет'}",
    ]
    if not caps.mass_search:
        lines.append("\n⚠️ Источник не умеет массовый поиск. Используйте /upload для импорта списка.")

    async with session_scope() as session:
        user = await repo.get_or_create_user(
            session, message.from_user.id, message.from_user.username
        )
        queries = await repo.list_queries(session, user.id)
    active = sum(1 for q in queries if q.is_active)
    lines.append(f"\nЗапросов: {len(queries)}, из них активных: {active}")

    await message.answer("\n".join(lines))


@router.message(Command("upload"))
async def cmd_upload(message: Message) -> None:
    await message.answer(
        "Импорт Excel/CSV появится на этапе 3.\n"
        "Сейчас источник списка — тот, что указан в PRIMARY_PROVIDER (см. /status)."
    )


@router.message(Command("export"))
async def cmd_export(message: Message) -> None:
    await message.answer("Выгрузка в Excel появится на этапе 3.")


@router.message(Command("pause_search"))
async def cmd_pause(message: Message) -> None:
    await message.answer("Откройте /searches и нажмите «Приостановить» у нужного запроса.")
