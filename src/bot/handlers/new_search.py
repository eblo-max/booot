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


# --- отрисовка шага ---------------------------------------------------------


def _step_kb(step_index: int, step: Step, selected: list) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for opt_index, (label, value) in enumerate(step.options):
        if step.multi:
            mark = "✅ " if value in selected else "▫️ "
            kb.button(text=mark + label, callback_data=f"ns:tog:{step_index}:{opt_index}")
        else:
            kb.button(text=label, callback_data=f"ns:opt:{step_index}:{opt_index}")
    kb.adjust(step.columns)

    nav = InlineKeyboardBuilder()
    if step.multi:
        nav.button(text="Готово ➡️", callback_data=f"ns:done:{step_index}")
    if step_index > 0:
        nav.button(text="⬅️ Назад", callback_data="ns:back")
    nav.button(text="✖️ Отмена", callback_data="ns:cancel")
    nav.adjust(2)
    kb.attach(nav)
    return kb.as_markup()


def _step_text(step_index: int, step: Step, edit_mode: bool) -> str:
    head = f"<b>{step.title}</b>" if edit_mode else f"<b>Шаг {step_index + 1}/{len(STEPS)}.</b>"
    text = f"{head} {step.prompt}"
    if step.hint:
        text += f"\n\n<i>{step.hint}</i>"
    elif step.parse:
        text += "\n\n<i>Можно ввести значение текстом.</i>"
    return text


async def _show_step(message: Message, state: FSMContext, step_index: int) -> None:
    data = await state.get_data()
    await state.set_state(Wizard.active)
    await state.update_data(step=step_index)

    step = STEPS[step_index]
    answers = data.get("answers", {})
    selected = answers.get(step.key, []) if step.multi else []
    await message.answer(
        _step_text(step_index, step, bool(data.get("edit_query_id"))),
        reply_markup=_step_kb(step_index, step, selected or []),
    )


# --- вход -------------------------------------------------------------------


@router.message(Command("new_search"))
async def cmd_new_search(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.update_data(answers={})
    await _show_step(message, state, 0)


@router.callback_query(F.data == "ns:cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Отменено.")
    await callback.answer()


@router.callback_query(F.data == "ns:back")
async def cb_back(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    step_index = max(0, int(data.get("step", 0)) - 1)
    await callback.message.delete()
    await _show_step(callback.message, state, step_index)
    await callback.answer()


# --- ответы -----------------------------------------------------------------


@router.callback_query(F.data.startswith("ns:opt:"))
async def cb_option(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, step_index_raw, opt_index_raw = callback.data.split(":")
    step = STEPS[int(step_index_raw)]
    label, value = step.options[int(opt_index_raw)]
    await _accept(callback.message, state, step, value, chosen=label, edit=callback.message)
    await callback.answer()


@router.callback_query(F.data.startswith("ns:tog:"))
async def cb_toggle(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, step_index_raw, opt_index_raw = callback.data.split(":")
    step_index = int(step_index_raw)
    step = STEPS[step_index]
    _, value = step.options[int(opt_index_raw)]

    data = await state.get_data()
    answers = dict(data.get("answers", {}))
    selected = list(answers.get(step.key) or [])
    if value in selected:
        selected.remove(value)
    else:
        selected.append(value)
    answers[step.key] = selected
    await state.update_data(answers=answers)

    await callback.message.edit_reply_markup(reply_markup=_step_kb(step_index, step, selected))
    await callback.answer()


@router.callback_query(F.data.startswith("ns:done:"))
async def cb_done(callback: CallbackQuery, state: FSMContext) -> None:
    step_index = int(callback.data.split(":")[2])
    step = STEPS[step_index]
    data = await state.get_data()
    selected = list((data.get("answers", {})).get(step.key) or [])
    chosen = ", ".join(str(s) for s in selected) if selected else "не важно"
    await _accept(callback.message, state, step, selected, chosen=chosen, edit=callback.message)
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

    if edit is not None and chosen is not None:
        try:
            await edit.edit_text(f"{step.title}: ✅ {chosen}")
        except Exception:  # сообщение могло быть уже изменено
            pass

    # в режиме правки возвращаемся на экран критериев, а не идём дальше по анкете
    if data.get("edit_query_id") or data.get("review"):
        await _show_review(message, state, answers)
        return

    step_index = int(data.get("step", 0)) + 1
    if step_index < len(STEPS):
        await _show_step(message, state, step_index)
    else:
        await _show_review(message, state, answers)


# --- сборка критериев -------------------------------------------------------


def _pick(answers: dict, key: str, default):
    """Явная проверка на None: answers.get(key, default) не спасает,
    когда ключ присутствует со значением None. Для False это критично."""
    value = answers.get(key)
    return default if value is None else value


def build_criteria(answers: dict) -> SearchCriteria:
    revenue_min, revenue_max = answers.get("revenue") or (None, None)

    period = answers.get("reg_period") or {}
    reg_last_days = period.get("days") if period.get("mode") == "relative" else None
    reg_from = period.get("from") if period.get("mode") == "absolute" else None
    reg_to = period.get("to") if period.get("mode") == "absolute" else None

    contacts = answers.get("contacts") or []
    return SearchCriteria(
        opf=answers.get("opf") or [],
        status=answers.get("status") or [],
        regions=answers.get("regions") or [],
        reg_date_from=reg_from,
        reg_date_to=reg_to,
        reg_last_days=reg_last_days,
        okved_main=answers.get("okved_main") or [],
        okved_match_mode=_pick(answers, "okved_match_mode", "main_only"),
        financial_year=answers.get("financial_year"),
        revenue_min=revenue_min,
        revenue_max=revenue_max,
        profit_min=answers.get("profit_min"),
        contacts_required="required" if "any" in contacts else "preferred",
        require_phone="phone" in contacts,
        require_email="email" in contacts,
        require_website="website" in contacts,
        special_tax_regimes=_pick(answers, "special_tax_regimes", "exclude"),
        allow_unknown_tax_status=_pick(answers, "allow_unknown_tax_status", True),
        schedule=_pick(answers, "schedule", "daily"),
        max_results_per_run=_pick(answers, "max_results_per_run", 50),
    )


def answers_from_criteria(criteria: SearchCriteria, name: str) -> dict:
    """Обратное преобразование — чтобы открыть сохранённый запрос на правку."""
    if criteria.reg_last_days is not None:
        period = {"mode": "relative", "days": criteria.reg_last_days}
    elif criteria.reg_date_from or criteria.reg_date_to:
        period = {"mode": "absolute", "from": criteria.reg_date_from, "to": criteria.reg_date_to}
    else:
        period = None

    contacts = []
    if criteria.require_phone:
        contacts.append("phone")
    if criteria.require_email:
        contacts.append("email")
    if criteria.require_website:
        contacts.append("website")
    if criteria.contacts_required == "required":
        contacts.append("any")

    return {
        "name": name,
        "regions": list(criteria.regions),
        "status": list(criteria.status),
        "opf": list(criteria.opf),
        "reg_period": period,
        "okved_main": list(criteria.okved_main),
        "okved_match_mode": criteria.okved_match_mode,
        "financial_year": criteria.financial_year,
        "revenue": (criteria.revenue_min, criteria.revenue_max),
        "profit_min": criteria.profit_min,
        "contacts": contacts,
        "special_tax_regimes": criteria.special_tax_regimes,
        "allow_unknown_tax_status": criteria.allow_unknown_tax_status,
        "schedule": criteria.schedule,
        "max_results_per_run": criteria.max_results_per_run,
    }


# --- экран критериев --------------------------------------------------------


async def _show_review(message: Message, state: FSMContext, answers: dict) -> None:
    criteria = build_criteria(answers)
    name = answers.get("name", "Без названия")
    query_id = (await state.get_data()).get("edit_query_id")

    await state.set_state(Wizard.confirm)
    await state.update_data(review=True, criteria=criteria.model_dump(mode="json"), name=name)

    lines = [f"<b>Запрос «{name}»</b>", ""] + [f"• {line}" for line in criteria.summary_lines()]
    lines += ["", "<i>Нажмите на критерий, чтобы изменить его.</i>"]

    kb = InlineKeyboardBuilder()
    for index, step in enumerate(STEPS):
        kb.button(text=step.title, callback_data=f"ns:goto:{index}")
    kb.adjust(3)

    actions = InlineKeyboardBuilder()
    actions.button(
        text="💾 Сохранить изменения" if query_id else "💾 Сохранить запрос",
        callback_data="ns:save",
    )
    actions.button(text="✖️ Отмена", callback_data="ns:cancel")
    actions.adjust(1, 1)
    kb.attach(actions)

    await message.answer("\n".join(lines), reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("ns:goto:"))
async def cb_goto(callback: CallbackQuery, state: FSMContext) -> None:
    step_index = int(callback.data.split(":")[2])
    await callback.message.delete()
    await _show_step(callback.message, state, step_index)
    await callback.answer()


@router.callback_query(F.data.startswith("q:edit:"))
async def cb_edit_query(callback: CallbackQuery, state: FSMContext) -> None:
    query_id = int(callback.data.split(":")[2])
    async with session_scope() as session:
        query = await repo.get_query(session, query_id)
        if query is None:
            await callback.answer("Запрос не найден", show_alert=True)
            return
        criteria = SearchCriteria.model_validate(query.criteria_json)
        answers = answers_from_criteria(criteria, query.name)

    await state.clear()
    await state.update_data(answers=answers, edit_query_id=query_id)
    await _show_review(callback.message, state, answers)
    await callback.answer()


@router.callback_query(F.data == "ns:save")
async def cb_save(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    criteria = SearchCriteria.model_validate(data["criteria"])
    name = data["name"]
    query_id = data.get("edit_query_id")

    async with session_scope() as session:
        if query_id:
            await repo.update_query(session, query_id, name, criteria)
        else:
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
        f"✅ Запрос «{name}» сохранён.", reply_markup=kb.as_markup()
    )
    await callback.answer()
    log.info("query_saved", query_id=query_id, user=callback.from_user.id)
