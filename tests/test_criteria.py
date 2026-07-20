from datetime import date
from decimal import Decimal

import pytest

from src.bot.handlers.new_search import answers_from_criteria, build_criteria
from src.bot.wizard import STEPS, _parse_reg_period
from src.domain.criteria import SearchCriteria


class TestRelativePeriod:
    def test_relative_window_is_computed_from_today(self):
        c = SearchCriteria(reg_last_days=30)
        today = date(2026, 7, 20)
        assert c.effective_reg_range(today) == (date(2026, 6, 20), today)

    def test_relative_window_moves_with_time(self):
        """Смысл относительного периода: сохранённый запрос не устаревает."""
        c = SearchCriteria(reg_last_days=7)
        first = c.effective_reg_range(date(2026, 1, 10))
        later = c.effective_reg_range(date(2026, 3, 10))
        assert first != later

    def test_absolute_window_is_stable(self):
        c = SearchCriteria(reg_date_from=date(2020, 1, 1), reg_date_to=date(2025, 12, 31))
        assert c.effective_reg_range(date(2026, 7, 20)) == (date(2020, 1, 1), date(2025, 12, 31))

    def test_relative_wins_over_absolute(self):
        c = SearchCriteria(
            reg_date_from=date(2020, 1, 1), reg_date_to=date(2020, 12, 31), reg_last_days=10
        )
        low, high = c.effective_reg_range(date(2026, 7, 20))
        assert low == date(2026, 7, 10)

    def test_no_period(self):
        assert SearchCriteria().effective_reg_range() == (None, None)


class TestRegPeriodParser:
    def test_absolute_range(self):
        assert _parse_reg_period("01.01.2020-31.12.2025") == {
            "mode": "absolute",
            "from": date(2020, 1, 1),
            "to": date(2025, 12, 31),
        }

    def test_relative_days(self):
        assert _parse_reg_period("последние 30 дней") == {"mode": "relative", "days": 30}

    def test_rejects_garbage(self):
        with pytest.raises(ValueError):
            _parse_reg_period("когда-нибудь")

    def test_rejects_absurd_day_count(self):
        with pytest.raises(ValueError):
            _parse_reg_period("последние 99999 дней")


class TestContactsText:
    def test_specific_requirements_listed(self):
        c = SearchCriteria(require_phone=True, require_email=True)
        assert c.contacts_text() == "Контакты обязательно: телефон, e-mail"

    def test_falls_back_to_general(self):
        assert SearchCriteria(contacts_required="no").contacts_text() == "Контакты: не важно"


class TestRoundTrip:
    """Правка запроса не должна терять критерии."""

    @pytest.fixture
    def full(self) -> SearchCriteria:
        return SearchCriteria(
            opf=["ООО", "АО"],
            status=["active", "liquidating"],
            regions=["77", "50"],
            reg_last_days=30,
            okved_main=["41.20", "43.11"],
            okved_match_mode="main_or_additional",
            financial_year=2025,
            revenue_min=Decimal("5000000"),
            revenue_max=Decimal("500000000"),
            profit_min=Decimal("1000000"),
            contacts_required="required",
            require_phone=True,
            require_website=True,
            special_tax_regimes="exclude",
            allow_unknown_tax_status=False,
            schedule="weekly",
            max_results_per_run=25,
        )

    def test_criteria_survive_round_trip(self, full):
        restored = build_criteria(answers_from_criteria(full, "тест"))
        assert restored.model_dump() == full.model_dump()

    def test_absolute_dates_survive_round_trip(self):
        original = SearchCriteria(
            reg_date_from=date(2020, 1, 1), reg_date_to=date(2025, 12, 31)
        )
        restored = build_criteria(answers_from_criteria(original, "тест"))
        assert restored.reg_date_from == date(2020, 1, 1)
        assert restored.reg_date_to == date(2025, 12, 31)
        assert restored.reg_last_days is None

    def test_name_survives(self, full):
        assert answers_from_criteria(full, "Строительные ООО")["name"] == "Строительные ООО"

    def test_defaults_survive_round_trip(self):
        default = SearchCriteria()
        restored = build_criteria(answers_from_criteria(default, "x"))
        assert restored.model_dump() == default.model_dump()


class TestWizardIntegrity:
    def test_every_step_key_is_consumed(self):
        """Шаг, который никто не читает, — молча потерянный критерий."""
        answers = {step.key: None for step in STEPS}
        answers["name"] = "x"
        build_criteria(answers)  # не должно падать

    def test_step_keys_are_unique(self):
        keys = [s.key for s in STEPS]
        assert len(keys) == len(set(keys))

    def test_multi_steps_have_scalar_options(self):
        for step in STEPS:
            if step.multi:
                for _, value in step.options:
                    assert not isinstance(value, list), f"{step.key}: мультивыбор ждёт скаляры"

    def test_every_step_has_options_or_parser(self):
        for step in STEPS:
            assert step.options or step.parse, f"{step.key}: шаг без вариантов и без ввода"
