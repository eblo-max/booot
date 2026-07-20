from datetime import date, timedelta
from decimal import Decimal

import pytest

from src.domain.company import CompanyDTO
from src.domain.criteria import SearchCriteria
from src.domain.matcher import CriteriaMatcher
from src.domain.tax_status import TaxStatus


@pytest.fixture
def criteria() -> SearchCriteria:
    """Критерии из примера пользователя: строительные ООО Москвы."""
    return SearchCriteria(
        opf=["ООО"],
        status=["active"],
        regions=["77"],
        reg_date_from=date(2020, 1, 1),
        reg_date_to=date(2025, 12, 31),
        okved_main=["41.20", "41.10", "42.11", "43.11", "43.12", "43.21"],
        financial_year=2025,
        revenue_min=Decimal("5000000"),
        revenue_max=Decimal("500000000"),
        contacts_required="preferred",
        special_tax_regimes="exclude",
    )


@pytest.fixture
def company() -> CompanyDTO:
    return CompanyDTO(
        inn="7707083893",
        ogrn="1027700132195",
        name='ООО "ПРИМЕР"',
        full_name='Общество с ограниченной ответственностью "ПРИМЕР"',
        status="active",
        region_code="77",
        registration_date=date(2021, 6, 18),
        main_okved="41.20",
        revenue_year=2025,
        revenue=Decimal("84500000"),
        profit=Decimal("4300000"),
        tax_status=TaxStatus.OSNO_PROBABLE,
        phones=["+74951234567"],
        source="test",
    )


class TestHappyPath:
    def test_reference_company_matches(self, criteria, company):
        assert CriteriaMatcher(criteria).match(company)


class TestRejections:
    def test_wrong_region(self, criteria, company):
        company.region_code = "50"
        result = CriteriaMatcher(criteria).match(company)
        assert not result and "region" in result.failed

    def test_registered_too_early(self, criteria, company):
        company.registration_date = date(2019, 12, 31)
        assert "registration_date" in CriteriaMatcher(criteria).match(company).failed

    def test_revenue_below_range(self, criteria, company):
        company.revenue = Decimal("4999999")
        assert "revenue" in CriteriaMatcher(criteria).match(company).failed

    def test_revenue_above_range(self, criteria, company):
        company.revenue = Decimal("500000001")
        assert "revenue" in CriteriaMatcher(criteria).match(company).failed

    def test_okved_sibling_rejected(self, criteria, company):
        company.main_okved = "41.21"
        assert "okved" in CriteriaMatcher(criteria).match(company).failed

    def test_special_regime_excluded(self, criteria, company):
        company.tax_status = TaxStatus.SPECIAL
        assert "tax_status" in CriteriaMatcher(criteria).match(company).failed

    def test_liquidated_rejected(self, criteria, company):
        company.status = "liquidated"
        assert "status" in CriteriaMatcher(criteria).match(company).failed

    def test_ip_rejected_when_ooo_required(self, criteria, company):
        company.opf = None
        company.name = "ИП Иванов Иван Иванович"
        company.full_name = "Индивидуальный предприниматель Иванов Иван Иванович"
        assert "opf" in CriteriaMatcher(criteria).match(company).failed


class TestUnknownData:
    """Неизвестное поле не отсеивает компанию молча — оно попадает в unverified."""

    def test_missing_revenue_is_unverified_not_failed(self, criteria, company):
        company.revenue = None
        result = CriteriaMatcher(criteria).match(company)
        assert result.matched
        assert "revenue" in result.unverified

    def test_unknown_tax_passes_by_default(self, criteria, company):
        company.tax_status = TaxStatus.UNKNOWN
        result = CriteriaMatcher(criteria).match(company)
        assert result.matched
        assert "tax_status" in result.unverified

    def test_unknown_tax_rejected_when_strict(self, criteria, company):
        criteria.allow_unknown_tax_status = False
        company.tax_status = TaxStatus.UNKNOWN
        assert "tax_status" in CriteriaMatcher(criteria).match(company).failed

    def test_revenue_for_other_year_is_not_confirmation(self, criteria, company):
        company.revenue_year = 2023
        result = CriteriaMatcher(criteria).match(company)
        assert result.matched
        assert "revenue_year" in result.unverified


class TestContacts:
    def test_required_contacts_reject_empty(self, criteria, company):
        criteria.contacts_required = "required"
        company.phones = []
        company.emails = []
        assert "contacts" in CriteriaMatcher(criteria).match(company).failed

    def test_preferred_contacts_allow_empty(self, criteria, company):
        company.phones = []
        company.emails = []
        assert CriteriaMatcher(criteria).match(company)


class TestSpecificContacts:
    def test_phone_required(self, criteria, company):
        criteria.require_phone = True
        company.phones = []
        company.emails = ["a@b.ru"]
        assert "phone" in CriteriaMatcher(criteria).match(company).failed

    def test_email_required(self, criteria, company):
        criteria.require_email = True
        company.emails = []
        assert "email" in CriteriaMatcher(criteria).match(company).failed

    def test_website_required(self, criteria, company):
        criteria.require_website = True
        company.website = None
        assert "website" in CriteriaMatcher(criteria).match(company).failed

    def test_all_present_passes(self, criteria, company):
        criteria.require_phone = criteria.require_email = criteria.require_website = True
        company.phones = ["+74951234567"]
        company.emails = ["a@b.ru"]
        company.website = "b.ru"
        assert CriteriaMatcher(criteria).match(company)


class TestRelativeRegistrationWindow:
    def test_recent_company_matches(self, company):
        c = SearchCriteria(opf=[], status=[], regions=[], reg_last_days=30)
        company.registration_date = date.today() - timedelta(days=5)
        assert CriteriaMatcher(c).match(company)

    def test_old_company_rejected(self, company):
        c = SearchCriteria(opf=[], status=[], regions=[], reg_last_days=30)
        company.registration_date = date.today() - timedelta(days=100)
        assert "registration_date" in CriteriaMatcher(c).match(company).failed

    def test_window_edge_is_inclusive(self, company):
        c = SearchCriteria(opf=[], status=[], regions=[], reg_last_days=30)
        company.registration_date = date.today() - timedelta(days=30)
        assert CriteriaMatcher(c).match(company)


class TestOkvedMatchMode:
    def test_main_only_ignores_additional(self, criteria, company):
        company.main_okved = "68.20"
        company.okved_list = ["68.20", "41.20"]
        assert "okved" in CriteriaMatcher(criteria).match(company).failed

    def test_additional_mode_accepts_secondary_code(self, criteria, company):
        criteria.okved_match_mode = "main_or_additional"
        company.main_okved = "68.20"
        company.okved_list = ["68.20", "41.20"]
        assert CriteriaMatcher(criteria).match(company)


class TestProfitThreshold:
    def test_below_threshold_rejected(self, criteria, company):
        criteria.profit_min = Decimal("5000000")
        company.profit = Decimal("4300000")
        assert "profit" in CriteriaMatcher(criteria).match(company).failed

    def test_missing_profit_is_unverified(self, criteria, company):
        criteria.profit_min = Decimal("5000000")
        company.profit = None
        result = CriteriaMatcher(criteria).match(company)
        assert result.matched and "profit" in result.unverified


class TestOpfDetection:
    def test_pao_not_confused_with_ao(self):
        c = SearchCriteria(opf=["АО"], status=[], regions=[])
        company = CompanyDTO(name='ПАО "ГАЗПРОМ"', full_name='Публичное акционерное общество "ГАЗПРОМ"')
        assert "opf" in CriteriaMatcher(c).match(company).failed

    def test_detects_ooo_from_full_name(self):
        c = SearchCriteria(opf=["ООО"], status=[], regions=[])
        company = CompanyDTO(name="ПРИМЕР", full_name="Общество с ограниченной ответственностью ПРИМЕР")
        assert CriteriaMatcher(c).match(company)
