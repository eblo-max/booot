"""Применение критериев к нормализованной компании.

Принцип: если провайдер не отдал поле (не знает), компания НЕ отсеивается молча —
она помечается как unverified по этому критерию. Иначе дешёвый источник тихо
выкидывал бы всё подряд.
"""

from dataclasses import dataclass, field

from src.domain.company import CompanyDTO
from src.domain.criteria import SearchCriteria
from src.domain.normalize import okved_matches
from src.domain.tax_status import TaxStatus

_OPF_ALIASES = {
    "ООО": ("общество с ограниченной ответственностью", "ооо"),
    "АО": ("акционерное общество", "ао"),
    "ПАО": ("публичное акционерное общество", "пао"),
    "НАО": ("непубличное акционерное общество", "нао"),
    "ИП": ("индивидуальный предприниматель", "ип"),
}


@dataclass
class MatchResult:
    matched: bool
    failed: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.matched


def _detect_opf(c: CompanyDTO) -> str | None:
    if c.opf:
        return c.opf.upper().strip()
    haystack = f"{c.full_name or ''} {c.name}".lower()
    # ПАО/НАО проверяем раньше АО, иначе "публичное акционерное" схлопнется в АО
    for code in ("ПАО", "НАО", "ООО", "АО", "ИП"):
        for alias in _OPF_ALIASES[code]:
            if alias in haystack:
                return code
    return None


class CriteriaMatcher:
    def __init__(self, criteria: SearchCriteria):
        self.c = criteria

    def match(self, company: CompanyDTO) -> MatchResult:
        failed: list[str] = []
        unverified: list[str] = []

        self._check_opf(company, failed, unverified)
        self._check_status(company, failed, unverified)
        self._check_region(company, failed, unverified)
        self._check_reg_date(company, failed, unverified)
        self._check_okved(company, failed, unverified)
        self._check_revenue(company, failed, unverified)
        self._check_profit(company, failed, unverified)
        self._check_contacts(company, failed, unverified)
        self._check_tax(company, failed, unverified)

        return MatchResult(matched=not failed, failed=failed, unverified=unverified)

    # --- отдельные критерии -------------------------------------------------

    def _check_opf(self, c: CompanyDTO, failed, unverified) -> None:
        if not self.c.opf:
            return
        opf = _detect_opf(c)
        if opf is None:
            unverified.append("opf")
        elif opf not in self.c.opf:
            failed.append("opf")

    def _check_status(self, c: CompanyDTO, failed, unverified) -> None:
        if not self.c.status:
            return
        if not c.status:
            unverified.append("status")
        elif c.status not in self.c.status:
            failed.append("status")

    def _check_region(self, c: CompanyDTO, failed, unverified) -> None:
        if not self.c.regions:
            return
        if not c.region_code:
            unverified.append("region")
        elif c.region_code.zfill(2) not in self.c.regions:
            failed.append("region")

    def _check_reg_date(self, c: CompanyDTO, failed, unverified) -> None:
        # относительное окно раскрывается здесь, на момент запуска
        date_from, date_to = self.c.effective_reg_range()
        if not (date_from or date_to):
            return
        if not c.registration_date:
            unverified.append("registration_date")
            return
        if date_from and c.registration_date < date_from:
            failed.append("registration_date")
        if date_to and c.registration_date > date_to:
            failed.append("registration_date")

    def _check_okved(self, c: CompanyDTO, failed, unverified) -> None:
        if not self.c.okved_main:
            return
        if self.c.okved_match_mode == "main_only":
            if not c.main_okved:
                unverified.append("okved")
            elif not okved_matches(c.main_okved, self.c.okved_main):
                failed.append("okved")
            return
        codes = ([c.main_okved] if c.main_okved else []) + c.okved_list
        if not codes:
            unverified.append("okved")
        elif not any(okved_matches(code, self.c.okved_main) for code in codes):
            failed.append("okved")

    def _check_revenue(self, c: CompanyDTO, failed, unverified) -> None:
        if self.c.revenue_min is None and self.c.revenue_max is None:
            return
        if c.revenue is None:
            unverified.append("revenue")
            return
        if self.c.financial_year and c.revenue_year and c.revenue_year != self.c.financial_year:
            # выручка есть, но за другой год — это не подтверждение критерия
            unverified.append("revenue_year")
            return
        if self.c.revenue_min is not None and c.revenue < self.c.revenue_min:
            failed.append("revenue")
        if self.c.revenue_max is not None and c.revenue > self.c.revenue_max:
            failed.append("revenue")

    def _check_profit(self, c: CompanyDTO, failed, unverified) -> None:
        if self.c.profit_min is None:
            return
        if c.profit is None:
            unverified.append("profit")
        elif c.profit < self.c.profit_min:
            failed.append("profit")

    def _check_contacts(self, c: CompanyDTO, failed, unverified) -> None:
        if self.c.contacts_required == "required" and not c.has_contacts:
            failed.append("contacts")
        if self.c.require_phone and not c.phones:
            failed.append("phone")
        if self.c.require_email and not c.emails:
            failed.append("email")
        if self.c.require_website and not c.website:
            failed.append("website")

    def _check_tax(self, c: CompanyDTO, failed, unverified) -> None:
        mode = self.c.special_tax_regimes
        if mode == "allow":
            return
        if mode == "only":
            if c.tax_status == TaxStatus.SPECIAL:
                return
            if c.tax_status == TaxStatus.UNKNOWN:
                unverified.append("tax_status")
                if not self.c.allow_unknown_tax_status:
                    failed.append("tax_status")
                return
            failed.append("tax_status")
            return
        # mode == "exclude"
        if c.tax_status == TaxStatus.SPECIAL:
            failed.append("tax_status")
        elif c.tax_status == TaxStatus.UNKNOWN:
            unverified.append("tax_status")
            if not self.c.allow_unknown_tax_status:
                failed.append("tax_status")
