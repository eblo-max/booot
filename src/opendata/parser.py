"""Потоковый разбор XML-выгрузок ФНС.

Файлы распаковываются в память по одному (каждый ~0.4 МБ), документы читаются
через iterparse с очисткой — полная выгрузка не помещается в память целиком.
"""

import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree as ET

import structlog

from src.domain.normalize import normalize_inn
from src.opendata.datasets import DatasetSpec

log = structlog.get_logger()


@dataclass
class FnsRecord:
    inn: str
    name: str
    data: dict
    actual_date: date | None


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%d.%m.%Y").date()
    except ValueError:
        return None


def _convert(spec: DatasetSpec, key: str, raw: str) -> object:
    if key in spec.bool_fields:
        return raw == "1"
    if key in spec.numeric_fields:
        try:
            return str(Decimal(raw.replace(",", ".")))
        except (InvalidOperation, AttributeError):
            return None
    return raw


def parse_xml_bytes(spec: DatasetSpec, payload: bytes) -> Iterator[FnsRecord]:
    """Разбирает один XML-файл выгрузки."""
    root = ET.fromstring(payload)
    for doc in root.iter("Документ"):
        np = doc.find("СведНП")
        if np is None:
            continue
        inn = normalize_inn(np.get("ИННЮЛ"))
        if not inn:
            # запись без валидного ИНН бесполезна: связать её не с чем
            continue

        block = doc.find(spec.payload_tag)
        data: dict = {}
        if block is not None:
            for attr, key in spec.fields.items():
                raw = block.get(attr)
                if raw is not None:
                    data[key] = _convert(spec, key, raw)

        yield FnsRecord(
            inn=inn,
            name=np.get("НаимОрг", ""),
            data=data,
            actual_date=_parse_date(doc.get("ДатаСост")),
        )


def parse_archive(spec: DatasetSpec, archive_path: Path) -> Iterator[FnsRecord]:
    """Разбирает zip-выгрузку целиком, файл за файлом."""
    with zipfile.ZipFile(archive_path) as archive:
        entries = [e for e in archive.namelist() if e.lower().endswith(".xml")]
        log.info("archive_opened", dataset=spec.code, files=len(entries))
        for index, entry in enumerate(entries, 1):
            with archive.open(entry) as handle:
                yield from parse_xml_bytes(spec, handle.read())
            if index % 200 == 0:
                log.info("archive_progress", dataset=spec.code, files_done=index, total=len(entries))
