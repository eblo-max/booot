"""data_hash и детектор существенных изменений.

Повторная отправка компании происходит только при изменении полей из SIGNIFICANT_FIELDS.
Обновление, например, даты выгрузки источника хеш не меняет.
"""

import hashlib
import json

from src.domain.company import CompanyDTO

SIGNIFICANT_FIELDS = (
    "status",
    "main_okved",
    "revenue",
    "profit",
    "tax_status",
    "manager_name",
    "has_contacts",
)

_LABELS = {
    "status": "сменился статус",
    "main_okved": "изменился основной ОКВЭД",
    "revenue": "изменилась выручка",
    "profit": "изменилась чистая прибыль",
    "tax_status": "изменился налоговый режим",
    "manager_name": "сменился руководитель",
    "has_contacts": "появились контакты",
}


def significant_snapshot(c: CompanyDTO) -> dict:
    return {
        "status": c.status,
        "main_okved": c.main_okved,
        "revenue": str(c.revenue) if c.revenue is not None else None,
        "profit": str(c.profit) if c.profit is not None else None,
        "tax_status": str(c.tax_status),
        "manager_name": c.manager_name,
        "has_contacts": c.has_contacts,
    }


def data_hash(c: CompanyDTO) -> str:
    payload = json.dumps(significant_snapshot(c), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def diff_reasons(old: dict | None, new: dict) -> list[str]:
    """Человекочитаемый список причин переотправки."""
    if not old:
        return []
    reasons = []
    for key in SIGNIFICANT_FIELDS:
        before, after = old.get(key), new.get(key)
        if before == after:
            continue
        if key == "has_contacts":
            # исчезновение контактов поводом для уведомления не считаем
            if after and not before:
                reasons.append(_LABELS[key])
            continue
        if key == "status" and after == "liquidating":
            reasons.append("компания ликвидируется")
            continue
        reasons.append(_LABELS[key])
    return reasons
