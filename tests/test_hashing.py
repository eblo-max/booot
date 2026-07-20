from decimal import Decimal

from src.domain.company import CompanyDTO
from src.domain.hashing import data_hash, diff_reasons, significant_snapshot
from src.domain.tax_status import TaxStatus


def make(**kw) -> CompanyDTO:
    base = dict(
        ogrn="1027700132195",
        name="ООО ПРИМЕР",
        status="active",
        main_okved="41.20",
        revenue=Decimal("84500000"),
        tax_status=TaxStatus.OSNO_PROBABLE,
    )
    return CompanyDTO(**{**base, **kw})


class TestHash:
    def test_stable_for_identical_data(self):
        assert data_hash(make()) == data_hash(make())

    def test_ignores_insignificant_fields(self):
        """Смена источника или даты выгрузки не должна вызывать переотправку."""
        from datetime import datetime

        a = make()
        b = make(source="other", source_updated_at=datetime(2026, 1, 1))
        assert data_hash(a) == data_hash(b)

    def test_changes_when_revenue_changes(self):
        assert data_hash(make()) != data_hash(make(revenue=Decimal("90000000")))

    def test_changes_when_contacts_appear(self):
        assert data_hash(make()) != data_hash(make(phones=["+74951234567"]))


class TestDiffReasons:
    def test_no_reasons_without_previous(self):
        assert diff_reasons(None, significant_snapshot(make())) == []

    def test_revenue_change(self):
        old = significant_snapshot(make())
        new = significant_snapshot(make(revenue=Decimal("90000000")))
        assert "изменилась выручка" in diff_reasons(old, new)

    def test_liquidating_has_own_wording(self):
        old = significant_snapshot(make())
        new = significant_snapshot(make(status="liquidating"))
        assert "компания ликвидируется" in diff_reasons(old, new)

    def test_contacts_appeared(self):
        old = significant_snapshot(make())
        new = significant_snapshot(make(phones=["+74951234567"]))
        assert "появились контакты" in diff_reasons(old, new)

    def test_contacts_disappeared_is_not_a_reason(self):
        old = significant_snapshot(make(phones=["+74951234567"]))
        new = significant_snapshot(make())
        assert diff_reasons(old, new) == []

    def test_manager_change(self):
        old = significant_snapshot(make(manager_name="Иванов И.И."))
        new = significant_snapshot(make(manager_name="Петров П.П."))
        assert "сменился руководитель" in diff_reasons(old, new)
