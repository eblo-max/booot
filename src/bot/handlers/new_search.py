import structlog
from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.bot.states import Wizard
from src.bot.wizard import STEPS, Step
from src.db import repositories as repo
from src.db.base import session_scope
from src.domain.criteria import SearchCriteria

log = structlog.get_logger()
router = Router()


def _kb(step_index: int, step: Step) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for opt_index, (label, _) in enumerate(step.options):
        kb.button(text=label, callback_data=f"ns:opt:{step_index}:{opt_index}")
    kb.adjust(step.columns)

    nav = InlineKeyboardBuilder()
    if step_index > 0:
        nav.button(text="⬅️ Назад", callback_data="ns:back")
    nav.button(text="✖️ Отмена", callback_data="ns:cancel")
    nav.adjust(2)
    kb.attach(nav)
    return kb.as_markup()


def _text(step: Step) -> str:
    text = step.prompt
    if step.hint:
        text += f"\n\n<i>{step.hint}</i>"
    elif step.parse:
        text += "\n\n<i>Можно ввести значение текстом.</i>"
    return text


async def _show_step(message: Message, state: FSMContext, step_index: int) -> None:
    await state.set_state(Wizard.active)
    await state.update_data(step=step_index)
    step = STEPS[step_index]
    await message.answer(_text(step), reply_markup=_kb(step_index, step))


@router.message(Command("new_search"))
async def cmd_new_search(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.update_data(answers={})
    await _show_step(message, state, 0)


@router.callback_query(F.data == "ns:cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Создание запроса отменено.")
    await callback.answer()


@router.callback_query(F.data == "ns:back")
async def cb_back(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    step_index = max(0, int(data.get("step", 0)) - 1)
    await callback.message.delete()
    await _show_step(callback.message, state, step_index)
    await callback.answer()


@router.callback_query(F.data.startswith("ns:opt:"))
async def cb_option(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, step_index_raw, opt_index_raw = callback.data.split(":")
    step_index, opt_index = int(step_index_raw), int(opt_index_raw)
    step = STEPS[step_index]
    label, value = step.options[opt_index]

    await _accept(callback.message, state, step, value, chosen=label, edit=callback.message)
    await callback.answer()


@router.message(StateFilter(Wizard.active), F.text, ~F.text.startswith("/"))
async def msg_answer(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if "step" not in data:
        return
    step = STEPS[int(data["step"])]
    if not step.parse:
        await message.answer("На этом шаге выберите вариант кнопкой.")
        return
    try:
        value = step.parse(message.text)
    except ValueError as exc:
        await message.answer(f"⚠️ {exc}")
        return
    await _accept(message, state, step, value)


async def _accept(
    message: Message,
    state: FSMContext,
    step: Step,
    value,
    chosen: str | None = None,
    edit=None,
) -> None:
    data = await state.get_data()
    answers = dict(data.get("answers", {}))
    answers[step.key] = value
    await state.update_data(answers=answers)

    if edit is not None:
        try:
            await edit.edit_text(f"{step.prompt}\n\n✅ {chosen}")
        except Exception:  # сообщение могло быть уже изменено
            pass

    step_index = int(data.get("step", 0)) + 1
    if step_index < len(STEPS):
        await _show_step(message, state, step_index)
    else:
        await _confirm(message, state, answers)


def build_criteria(answers: dict) -> SearchCriteria:
    revenue_min, revenue_max = answers.get("revenue") or (None, None)
    return SearchCriteria(
        opf=answers.get("opf") or [],
        status=answers.get("status") or [],
        regions=answers.get("regions") or [],
        reg_date_from=answers.get("reg_date_from"),
        reg_date_to=answers.get("reg_date_to"),
        okved_main=answers.get("okved_main") or [],
        financial_year=answers.get("financial_year"),
        revenue_min=revenue_min,
        revenue_max=revenue_max,
        contacts_required=answers.get("contacts_required", "preferred"),
        special_tax_regimes=answers.get("special_tax_regimes", "exclude"),
        schedule=answers.get("schedule", "daily"),
        max_results_per_run=answers.get("max_results_per_run", 50),
    )


async def _confirm(message: Message, state: FSMContext, answers: dict) -> None:
    criteria = build_criteria(answers)
    name = answers.get("name", "Без названия")

    lines = [f"<b>Запрос «{name}»</b>", ""] + [f"• {line}" for line in criteria.summary_lines()]

    kb = InlineKeyboardBuilder()
    kb.button(text="💾 Сохранить", callback_data="ns:save")
    kb.button(text="✖️ Отмена", callback_data="ns:cancel")
    kb.adjust(2)

    await state.set_state(Wizard.confirm)
    await state.update_data(criteria=criteria.model_dump(mode="json"), name=name)
    await message.answer("\n".join(lines), reply_markup=kb.as_markup())


@router.callback_query(F.data == "ns:save")
async def cb_save(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    criteria = SearchCriteria.model_validate(data["criteria"])
    name = data["name"]

    async with session_scope() as session:
        user = await repo.get_or_create_user(
            session, callback.from_user.id, callback.from_user.username
        )
        query = await repo.create_query(session, user.id, name, criteria)
        query_id = query.id

    await state.clear()

    kb = InlineKeyboardBuilder()
    kb.button(text="▶️ Запустить сейчас", callback_data=f"q:run:{query_id}")
    kb.button(text="📋 Мои запросы", callback_data="q:list")
    kb.adjust(1)

    await callback.message.edit_text(
        f"✅ Запрос «{name}» сохранён.\n\nМожно запустить его прямо сейчас.",
        reply_markup=kb.as_markup(),
    )
    await callback.answer()
    log.info("query_created", query_id=query_id, user=callback.from_user.id)
