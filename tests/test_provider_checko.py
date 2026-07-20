"""Разбор ответов Checko.

Фикстуры собраны по официальной документации ofdata.ru/api. Проверить их на
живом ключе ещё предстоит — у Checko нет публичного тестового ключа, в отличие
от DataNewton, чей адаптер сверен с настоящим ответом API.
"""

from datetime import date

import pytest

from src.domain.criteria import SearchCriteria
from src.domain.tax_status import TaxStatus
from src.providers.checko import CheckoProvider
from src.providers.exceptions import MassSearchNotSupported

COMPANY = {
    "ОГРН": "1027700132195",
    "ИНН": "7707083893",
    "КПП": "773601001",
    "НаимСокр": 'ПАО "СБЕРБАНК"',
    "НаимПолн": 'ПУБЛИЧНОЕ АКЦИОНЕРНОЕ ОБЩЕСТВО "СБЕРБАНК РОССИИ"',
    "ДатаРег": "1991-06-20",
    "Статус": {"Код": "001", "Наим": "Действующая организация"},
    "Регион": {"Код": "77", "Наим": "Москва"},
    "ОКВЭД": {"Код": "64.19", "Наим": "Денежное посредничество прочее"},
    "ОКВЭДДоп": [{"Код": "62.09"}],
    "Руковод": [{"ФИО": "Греф Герман Оскарович", "НаимДолжн": "Президент"}],
    "Контакты": {
        "Тел": ["+7 (495) 500-55-50", "8 800 555-55-50"],
        "Емэйл": ["Info@sberbank.ru"],
        "ВебСайт": "https://www.sberbank.ru/",
    },
    "Налоги": {"ОсобРежим": []},
}


@pytest.fixture
def provider():
    return CheckoProvider("test", fetch_details=False)


class TestCompanyCard:
    def test_identifiers(self, provider):
        dto = provider.normalize_response(COMPANY)
        assert dto.inn == "7707083893" and dto.ogrn == "1027700132195"

    def test_status_mapped(self, provider):
        assert provider.normalize_response(COMPANY).status == "active"

    def test_region(self, provider):
        dto = provider.normalize_response(COMPANY)
        assert dto.region_code == "77" and dto.region_name == "Москва"

    def test_registration_date(self, provider):
        assert provider.normalize_response(COMPANY).registration_date == date(1991, 6, 20)

    def test_okved(self, provider):
        dto = provider.normalize_response(COMPANY)
        assert dto.main_okved == "64.19" and "62.09" in dto.okved_list

    def test_contacts_normalized(self, provider):
        dto = provider.normalize_response(COMPANY)
        assert "+74955005550" in dto.phones
        assert dto.emails == ["info@sberbank.ru"]
        assert dto.website == "sberbank.ru"

    def test_manager(self, provider):
        assert provider.normalize_response(COMPANY).manager_name.startswith("Греф")

    def test_opf(self, provider):
        assert provider.normalize_response(COMPANY).opf == "ПАО"


class TestTaxRegimes:
    def test_empty_special_list_is_probable_osno(self, provider):
        """Блок налогов пришёл и спецрежимов в нём нет — основание для «вероятной ОСНО»."""
        assert provider.normalize_response(COMPANY).tax_status is TaxStatus.OSNO_PROBABLE

    def test_usn_detected(self, provider):
        payload = {**COMPANY, "Налоги": {"ОсобРежим": ["УСН"]}}
        dto = provider.normalize_response(payload)
        assert dto.tax_status is TaxStatus.SPECIAL and dto.tax_regimes == ["УСН"]

    def test_missing_tax_block_is_unknown(self, provider):
        """Ответ поиска налогов не содержит — врать про ОСНО нельзя."""
        payload = {k: v for k, v in COMPANY.items() if k != "Налоги"}
        assert provider.normalize_response(payload).tax_status is TaxStatus.UNKNOWN


class TestStatusVariants:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Действующая организация", "active"),
            ("В процессе ликвидации", "liquidating"),
            ("Ликвидирована", "liquidated"),
            ("Прекращена деятельность", "liquidated"),
            ("В процессе реорганизации", "reorganizing"),
        ],
    )
    def test_status_text_mapping(self, provider, text, expected):
        dto = provider.normalize_response({**COMPANY, "Статус": {"Наим": text}})
        assert dto.status == expected

    def test_liquidation_date_wins(self, provider):
        dto = provider.normalize_response({**COMPANY, "ДатаЛикв": "2020-01-01"})
        assert dto.status == "liquidated"


class TestSearchContract:
    async def test_search_without_okved_is_refused(self, provider):
        """Честный отказ вместо тихой выдачи мусора."""
        criteria = SearchCriteria(okved_main=[])
        with pytest.raises(MassSearchNotSupported):
            await provider.search(criteria, limit=10)

    def test_cursor_roundtrip(self, provider):
        assert provider._parse_cursor(None) == (0, 1)
        assert provider._parse_cursor("2:5") == (2, 5)

    def test_declares_local_filters(self, provider):
        """Выручку и контакты Checko не фильтрует — это должно быть видно в capabilities."""
        caps = provider.capabilities
        assert "revenue" not in caps.supported_filters
        assert "contacts" not in caps.supported_filters

        criteria = SearchCriteria(
            regions=["77"], okved_main=["41.20"], revenue_min=1, contacts_required="required"
        )
        assert "revenue" in provider.unsupported_filters(criteria)
