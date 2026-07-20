"""Регрессия: секция состояния индекса ФНС в /status.

Первая версия использовала all(...) с await внутри генератора, что создаёт
асинхронный генератор — all() его не принимает, и /status молча падал
с TypeError, не отвечая пользователю вообще ничего.
"""

from datetime import date, datetime

from src.bot.handlers.common import _opendata_lines


class FakeState:
    def __init__(self, **kw):
        self.file_id = None
        self.actual_date = None
        self.records_count = 0
        self.is_complete = False
        self.loaded_at = None
        self.error_message = None
        for k, v in kw.items():
            setattr(self, k, v)


class FakeSession:
    """Отдаёт состояние набора по его коду."""

    def __init__(self, states: dict):
        self.states = states

    async def get(self, _model, code):
        return self.states.get(code)


class TestOpendataLines:
    async def test_empty_index_reports_unknown(self):
        lines = await _opendata_lines(FakeSession({}))
        assert any("Индекс пуст" in line for line in lines)
        assert any("не загружен" in line for line in lines)

    async def test_loaded_dataset_is_listed(self):
        session = FakeSession(
            {
                "snr": FakeState(
                    is_complete=True,
                    loaded_at=datetime.now(),
                    records_count=1_400_000,
                    actual_date=date(2026, 6, 1),
                )
            }
        )
        lines = await _opendata_lines(session)
        text = "\n".join(lines)
        assert "✅" in text
        assert "1 400 000" in text
        assert "Индекс пуст" not in text

    async def test_incomplete_dataset_is_flagged(self):
        session = FakeSession(
            {"snr": FakeState(is_complete=False, loaded_at=datetime.now(), records_count=5)}
        )
        text = "\n".join(await _opendata_lines(session))
        assert "неполный" in text

    async def test_dataset_in_progress(self):
        """Строка создана, но загрузка не завершена — не «не загружен»."""
        session = FakeSession({"snr": FakeState(loaded_at=None)})
        text = "\n".join(await _opendata_lines(session))
        assert "загружается" in text

    async def test_returns_plain_list_of_strings(self):
        lines = await _opendata_lines(FakeSession({}))
        assert isinstance(lines, list)
        assert all(isinstance(line, str) for line in lines)
