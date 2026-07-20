"""Налоговый режим. Ключевое правило: отсутствие данных != отсутствие спецрежима."""

from enum import StrEnum


class TaxStatus(StrEnum):
    OSNO_CONFIRMED = "osno_confirmed"
    OSNO_PROBABLE = "osno_probable"
    SPECIAL = "special"
    UNKNOWN = "unknown"


SPECIAL_REGIME_CODES = {"УСН", "АУСН", "ЕСХН", "ПСН", "НПД", "ЕНВД", "СРП"}


def resolve_tax_status(
    *,
    registry_checked: bool,
    found_regimes: list[str] | None = None,
    source_states_osno: bool = False,
) -> TaxStatus:
    """
    registry_checked   — реестр спецрежимов реально опрошен и ответил
    found_regimes      — найденные спецрежимы
    source_states_osno — источник ЯВНО сообщает, что применяется ОСНО
    """
    if source_states_osno:
        return TaxStatus.OSNO_CONFIRMED
    if found_regimes:
        return TaxStatus.SPECIAL
    if registry_checked:
        # реестр опрошен, спецрежимов нет -> ОСНО вероятна, но не подтверждена
        return TaxStatus.OSNO_PROBABLE
    return TaxStatus.UNKNOWN


def describe(status: TaxStatus, regimes: list[str] | None = None, source: str | None = None) -> str:
    """Человекочитаемая формулировка для карточки. Никаких домыслов."""
    match status:
        case TaxStatus.OSNO_CONFIRMED:
            src = f" (источник: {source})" if source else ""
            return f"ОСНО подтверждена{src}"
        case TaxStatus.OSNO_PROBABLE:
            return "Вероятная ОСНО — специальные режимы не обнаружены"
        case TaxStatus.SPECIAL:
            listed = ", ".join(regimes) if regimes else "спецрежим"
            src = f" (по данным {source})" if source else ""
            return f"{listed}{src}"
        case _:
            return "Налоговый режим неизвестен"
