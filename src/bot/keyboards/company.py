from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def company_kb(result_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Подробнее", callback_data=f"co:info:{result_id}")
    kb.button(text="🔍 Найти в вебе", callback_data=f"co:web:{result_id}")
    kb.button(text="⭐ В избранное", callback_data=f"co:fav:{result_id}")
    kb.button(text="🚫 Не показывать", callback_data=f"co:hide:{result_id}")
    kb.button(text="🔄 Проверить повторно", callback_data=f"co:recheck:{result_id}")
    kb.adjust(2, 2, 1)
    return kb.as_markup()


def more_results_kb(query_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Показать ещё 10", callback_data=f"q:more:{query_id}")]
        ]
    )


def query_kb(query_id: int, is_active: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="▶️ Запустить сейчас", callback_data=f"q:run:{query_id}")
    if is_active:
        kb.button(text="⏸ Приостановить", callback_data=f"q:pause:{query_id}")
    else:
        kb.button(text="▶️ Продолжить", callback_data=f"q:resume:{query_id}")
    kb.button(text="📋 Показать результаты", callback_data=f"q:results:{query_id}")
    kb.button(text="📊 Выгрузить Excel", callback_data=f"q:export:{query_id}")
    kb.button(text="✏️ Изменить", callback_data=f"q:edit:{query_id}")
    kb.button(text="🗑 Удалить", callback_data=f"q:del:{query_id}")
    kb.adjust(1, 2, 2, 1)
    return kb.as_markup()


def confirm_delete_kb(query_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да, удалить", callback_data=f"q:delyes:{query_id}"),
                InlineKeyboardButton(text="Отмена", callback_data=f"q:card:{query_id}"),
            ]
        ]
    )
