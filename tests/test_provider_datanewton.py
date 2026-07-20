"""Разбор проверяется на настоящем ответе DataNewton (ПАО Сбербанк, июль 2026)."""

import json
from datetime import date
from pathlib import Path

import pytest

from src.domain.tax_status import TaxStatus
from src.providers.datanewton import DataNewtonProvider

FIXTURE = Path(__file__).parent / "fixtures" / "datanewton_company.json"


@pytest.fixture
def dto():
    provider = DataNewtonProvider("test")
    return provider.normalize_response(json.loads(FIXTURE.read_text(encoding="utf-8")))


class TestRealResponse:
    def test_identifiers(self, dto):
        assert dto.inn == "7707083893"
        assert dto.ogrn == "1027700132195"

    def test_names(self, dto):
        assert dto.name == "ПАО СБЕРБАНК"
        assert "СБЕРБАНК РОССИИ" in dto.full_name

    def test_opf_detected_as_pao(self, dto):
        assert dto.opf == "ПАО"

    def test_status(self, dto):
        assert dto.status == "active"

    def test_region_from_address_block(self, dto):
        assert dto.region_code == "77"
        assert dto.region_name == "Москва"

    def test_registration_date(self, dto):
        assert dto.registration_date == date(1991, 6, 20)

    def test_main_okved_is_the_one_flagged_main(self, dto):
        assert dto.main_okved == "64.19"

    def test_additional_okveds_collected(self, dto):
        assert "62.09" in dto.okved_list

    def test_emails_normalized(self, dto):
        assert dto.emails
        assert all("@" in e and e == e.lower() for e in dto.emails)

    def test_noisy_contacts_are_trimmed(self, dto):
        """В живом ответе у Сбербанка 50 почт, почти все — чужие, подтянутые
        парсером с посторонних сайтов. Отдаём только верхушку."""
        assert len(dto.emails) <= 5

    def test_egrul_sourced_email_wins(self, dto):
        """Почта из ЕГРЮЛ достовернее найденной на сайте — она должна быть первой."""
        assert dto.emails[0] == "gref_p@sberbank.ru"

    def test_emails_deduplicated(self, dto):
        assert len(dto.emails) == len(set(dto.emails))

    def test_raw_payload_preserved(self, dto):
        assert dto.raw["inn"] == "7707083893"


class TestTaxMode:
    def test_common_mode_gives_confirmed_osno(self, dto):
        """DataNewton прямо сообщает common_mode — это подтверждение, а не догадка."""
        assert dto.tax_status is TaxStatus.OSNO_CONFIRMED
        assert dto.tax_source == "DataNewton"

    def test_special_regime_wins_over_common_mode(self):
        provider = DataNewtonProvider("test")
        payload = {
            "inn": "7707083893",
            "ogrn": "1027700132195",
            "company": {
                "company_names": {"short_name": 'ООО "X"'},
                "tax_mode_info": {"usn_sign": True, "common_mode": True},
            },
        }
        dto = provider.normalize_response(payload)
        assert dto.tax_status is TaxStatus.SPECIAL
        assert dto.tax_regimes == ["УСН"]

    def test_missing_tax_block_is_unknown(self):
        provider = DataNewtonProvider("test")
        payload = {"inn": "7707083893", "company": {"company_names": {"short_name": "X"}}}
        assert provider.normalize_response(payload).tax_status is TaxStatus.UNKNOWN

    def test_no_regimes_and_no_common_mode_is_probable(self):
        provider = DataNewtonProvider("test")
        payload = {
            "inn": "7707083893",
            "company": {
                "company_names": {"short_name": "X"},
                "tax_mode_info": {"usn_sign": False, "common_mode": False},
            },
        }
        assert provider.normalize_response(payload).tax_status is TaxStatus.OSNO_PROBABLE


class TestCapabilities:
    def test_declares_contacts_and_tax(self):
        caps = DataNewtonProvider("test").capabilities
        assert caps.has_contacts and caps.has_tax_regime and caps.has_financials

    def test_empty_payload_does_not_crash(self):
        dto = DataNewtonProvider("test").normalize_response({})
        assert dto.inn is None and dto.name == ""
