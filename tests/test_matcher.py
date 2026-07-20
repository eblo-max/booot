from datetime import date
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


class TestOpfDetection:
    def test_pao_not_confused_with_ao(self):
        c = SearchCriteria(opf=["АО"], status=[], regions=[])
        company = CompanyDTO(name='ПАО "ГАЗПРОМ"', full_name='Публичное акционерное общество "ГАЗПРОМ"')
        assert "opf" in CriteriaMatcher(c).match(company).failed

    def test_detects_ooo_from_full_name(self):
        c = SearchCriteria(opf=["ООО"], status=[], regions=[])
        company = CompanyDTO(name="ПРИМЕР", full_name="Общество с ограниченной ответственностью ПРИМЕР")
        assert CriteriaMatcher(c).match(company)
