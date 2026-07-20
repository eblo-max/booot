"""Нормализация и валидация реквизитов. ИНН/ОГРН проверяются по контрольным суммам."""

import re

_DIGITS = re.compile(r"\D")

_INN10_W = [2, 4, 10, 3, 5, 9, 4, 6, 8]
_INN12_W1 = [7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
_INN12_W2 = [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]


def digits(value: str | None) -> str:
    return _DIGITS.sub("", value or "")


def normalize_inn(value: str | None) -> str | None:
    """Возвращает ИНН, если он валиден по контрольной сумме, иначе None."""
    inn = digits(value)
    if len(inn) == 10:
        return inn if _checksum(inn, _INN10_W) == int(inn[9]) else None
    if len(inn) == 12:
        ok = _checksum(inn, _INN12_W1) == int(inn[10]) and _checksum(inn, _INN12_W2) == int(inn[11])
        return inn if ok else None
    return None


def normalize_ogrn(value: str | None) -> str | None:
    """ОГРН (13 знаков) или ОГРНИП (15). Контрольный разряд — остаток от деления."""
    ogrn = digits(value)
    if len(ogrn) == 13:
        control = int(ogrn[:12]) % 11 % 10
        return ogrn if control == int(ogrn[12]) else None
    if len(ogrn) == 15:
        control = int(ogrn[:14]) % 13 % 10
        return ogrn if control == int(ogrn[14]) else None
    return None


def _checksum(inn: str, weights: list[int]) -> int:
    return sum(int(d) * w for d, w in zip(inn, weights, strict=False)) % 11 % 10


def region_from_inn(inn: str | None) -> str | None:
    inn = digits(inn)
    return inn[:2] if len(inn) >= 2 else None


def normalize_phone(value: str | None) -> str | None:
    """Приводит к +7XXXXXXXXXX. Мусор и короткие номера отбрасывает."""
    d = digits(value)
    if len(d) == 11 and d[0] in "78":
        return "+7" + d[1:]
    if len(d) == 10:
        return "+7" + d
    return None


def normalize_phones(values: list[str] | str | None) -> list[str]:
    if not values:
        return []
    if isinstance(values, str):
        values = re.split(r"[;,\n]", values)
    out = []
    for v in values:
        p = normalize_phone(v)
        if p and p not in out:
            out.append(p)
    return out


_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Zа-яА-Я]{2,}$")


def normalize_emails(values: list[str] | str | None) -> list[str]:
    if not values:
        return []
    if isinstance(values, str):
        values = re.split(r"[;,\s]+", values)
    out = []
    for v in values:
        e = (v or "").strip().lower()
        if _EMAIL.match(e) and e not in out:
            out.append(e)
    return out


def normalize_okved(value: str | None) -> str | None:
    """41,20 -> 41.20 ; '41.20 Строительство...' -> 41.20"""
    if not value:
        return None
    m = re.match(r"\s*(\d{2}(?:\.\d{1,2}){0,2})", str(value).replace(",", "."))
    return m.group(1) if m else None


def okved_matches(code: str | None, patterns: list[str]) -> bool:
    """Иерархическое сравнение по классификатору ОКВЭД.

    41    матчит 41.20  (раздел покрывает класс)
    41.2  матчит 41.20  (группа покрывает подгруппу)
    41.20 матчит 41.20.1
    41.20 НЕ матчит 41.21 — это соседние коды, а не вложенные.
    """
    if not patterns:
        return True
    code = normalize_okved(code)
    if not code:
        return False
    code_parts = code.split(".")
    return any(_hierarchy_match(code_parts, pat) for pat in patterns)


def _hierarchy_match(code_parts: list[str], raw_pattern: str) -> bool:
    pat = normalize_okved(raw_pattern)
    if not pat:
        return False
    pat_parts = pat.split(".")
    if len(pat_parts) > len(code_parts):
        return False
    # раздел (первые 2 цифры) всегда сравниваем точно
    if pat_parts[0] != code_parts[0]:
        return False
    # промежуточные сегменты — точное совпадение
    if pat_parts[1:-1] != code_parts[1 : len(pat_parts) - 1]:
        return False
    if len(pat_parts) == 1:
        return True
    # последний сегмент шаблона может быть началом сегмента кода: 2 покрывает 20
    return code_parts[len(pat_parts) - 1].startswith(pat_parts[-1])


def normalize_website(value: str | None) -> str | None:
    if not value:
        return None
    site = str(value).strip().lower()
    site = re.sub(r"^https?://", "", site)
    site = re.sub(r"^www\.", "", site).rstrip("/")
    return site or None
