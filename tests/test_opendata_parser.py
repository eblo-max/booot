"""Парсер проверяется на фикстуре, вырезанной из настоящей выгрузки ФНС
(набор «Специальные налоговые режимы», файл от 25.06.2026)."""

from pathlib import Path

import pytest

from src.opendata.datasets import SNR
from src.opendata.parser import parse_xml_bytes

FIXTURE = Path(__file__).parent / "fixtures" / "snr_sample.xml"


@pytest.fixture
def records():
    return list(parse_xml_bytes(SNR, FIXTURE.read_bytes()))


class TestRealFnsData:
    def test_all_documents_parsed(self, records):
        assert len(records) == 6

    def test_inn_extracted_and_valid(self, records):
        assert all(r.inn and len(r.inn) == 10 for r in records)

    def test_name_extracted(self, records):
        assert all(r.name.startswith("ОБЩЕСТВО") for r in records)

    def test_actual_date_parsed(self, records):
        assert all(r.actual_date is not None for r in records)
        assert all(r.actual_date.year == 2026 for r in records)

    def test_flags_are_booleans(self, records):
        for r in records:
            assert isinstance(r.data["usn"], bool)
            assert isinstance(r.data["ausn"], bool)

    def test_every_record_has_at_least_one_regime(self, records):
        """Ключевое свойство набора: в нём только компании со спецрежимом.
        На нём держится вывод «вероятная ОСНО» для отсутствующих ИНН."""
        for r in records:
            assert any(r.data[k] for k in ("usn", "ausn", "eshn", "srp")), r.inn


class TestRobustness:
    def test_record_without_inn_is_skipped(self):
        xml = (
            '<Файл><Документ ДатаСост="01.06.2026">'
            '<СведНП НаимОрг="БЕЗ ИНН"/>'
            '<СведСНР ПризнУСН="1" ПризнАУСН="0" ПризнЕСХН="0" ПризнСРП="0"/>'
            "</Документ></Файл>"
        ).encode()
        assert list(parse_xml_bytes(SNR, xml)) == []

    def test_invalid_inn_is_skipped(self):
        xml = (
            '<Файл><Документ ДатаСост="01.06.2026">'
            '<СведНП НаимОрг="БИТЫЙ ИНН" ИННЮЛ="7703023101"/>'
            '<СведСНР ПризнУСН="1" ПризнАУСН="0" ПризнЕСХН="0" ПризнСРП="0"/>'
            "</Документ></Файл>"
        ).encode()
        assert list(parse_xml_bytes(SNR, xml)) == []

    def test_missing_payload_block_yields_empty_data(self):
        xml = (
            '<Файл><Документ ДатаСост="01.06.2026">'
            '<СведНП НаимОрг="НЕТ БЛОКА" ИННЮЛ="7703023100"/>'
            "</Документ></Файл>"
        ).encode()
        records = list(parse_xml_bytes(SNR, xml))
        assert len(records) == 1 and records[0].data == {}

    def test_bad_date_does_not_crash(self):
        xml = (
            '<Файл><Документ ДатаСост="не дата">'
            '<СведНП НаимОрг="X" ИННЮЛ="7703023100"/>'
            '<СведСНР ПризнУСН="1" ПризнАУСН="0" ПризнЕСХН="0" ПризнСРП="0"/>'
            "</Документ></Файл>"
        ).encode()
        assert list(parse_xml_bytes(SNR, xml))[0].actual_date is None


class TestNumericDataset:
    def test_revenue_parsed_as_string_decimal(self):
        from src.opendata.datasets import REVEXP

        xml = (
            '<Файл><Документ ДатаСост="01.06.2026">'
            '<СведНП НаимОрг="X" ИННЮЛ="7703023100"/>'
            '<СведДохРасх СумДоход="84500000.50" СумРасход="80000000"/>'
            "</Документ></Файл>"
        ).encode()
        data = list(parse_xml_bytes(REVEXP, xml))[0].data
        assert data["revenue"] == "84500000.50"
        assert data["expenses"] == "80000000"
