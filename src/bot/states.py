from aiogram.fsm.state import State, StatesGroup


class Wizard(StatesGroup):
    """Мастер /new_search. Один state на все шаги — номер шага лежит в data."""

    active = State()
    confirm = State()


class Upload(StatesGroup):
    waiting_file = State()
