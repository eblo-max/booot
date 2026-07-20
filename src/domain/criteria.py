from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator

CriteriaOPF = Literal["ООО", "АО", "ПАО", "НАО", "ИП"]
CompanyStatus = Literal["active", "liquidating", "liquidated", "reorganizing"]


class SearchCriteria(BaseModel):
    """Сериализуется в search_queries.criteria_json."""

    version: int = 1

    opf: list[CriteriaOPF] = Field(default_factory=lambda: ["ООО"])
    status: list[CompanyStatus] = Field(default_factory=lambda: ["active"])
    regions: list[str] = Field(default_factory=list)  # коды ФНС: ["77", "50"]

    reg_date_from: date | None = None
    reg_date_to: date | None = None

    okved_main: list[str] = Field(default_factory=list)
    okved_match_mode: Literal["main_only", "main_or_additional"] = "main_only"

    financial_year: int | None = None
    revenue_min: Decimal | None = None
    revenue_max: Decimal | None = None
    profit_min: Decimal | None = None

    contacts_required: Literal["no", "preferred", "required"] = "preferred"
    special_tax_regimes: Literal["exclude", "allow", "only"] = "exclude"
    # пускать ли компании с неизвестным налоговым режимом при special_tax_regimes="exclude"
    allow_unknown_tax_status: bool = True

    schedule: Literal["daily", "weekly", "manual"] = "daily"
    max_results_per_run: int = Field(default=50, ge=1, le=500)
    only_new: bool = True

    @field_validator("regions", mode="before")
    @classmethod
    def _pad_regions(cls, v):
        if not v:
            return []
        return [str(x).strip().zfill(2) for x in v]

    @field_validator("okved_main", mode="before")
    @classmethod
    def _clean_okved(cls, v):
        if not v:
            return []
        out = []
        for code in v:
            code = str(code).strip().replace(",", ".")
            if code:
                out.append(code)
        return out

    def summary_lines(self) -> list[str]:
        """Сводка критериев для экрана подтверждения."""
        lines = [
            f"ОПФ: {', '.join(self.opf) if self.opf else 'любая'}",
            f"Статус: {'действующие' if self.status == ['active'] else ', '.join(self.status)}",
            f"Регионы: {', '.join(self.regions) if self.regions else 'вся РФ'}",
        ]
        if self.reg_date_from or self.reg_date_to:
            frm = self.reg_date_from.strftime("%d.%m.%Y") if self.reg_date_from else "—"
            to = self.reg_date_to.strftime("%d.%m.%Y") if self.reg_date_to else "—"
            lines.append(f"Регистрация: {frm} — {to}")
        if self.okved_main:
            lines.append(f"ОКВЭД: {', '.join(self.okved_main)}")
        if self.revenue_min is not None or self.revenue_max is not None:
            frm = f"{self.revenue_min:,.0f}".replace(",", " ") if self.revenue_min else "0"
            to = f"{self.revenue_max:,.0f}".replace(",", " ") if self.revenue_max else "∞"
            year = f" за {self.financial_year}" if self.financial_year else ""
            lines.append(f"Выручка{year}: {frm} — {to} ₽")
        lines.append(
            {
                "no": "Контакты: не важно",
                "preferred": "Контакты: желательно",
                "required": "Контакты: обязательно",
            }[self.contacts_required]
        )
        lines.append(
            {
                "exclude": "Спецрежимы: исключить",
                "allow": "Спецрежимы: допустимы",
                "only": "Спецрежимы: только они",
            }[self.special_tax_regimes]
        )
        lines.append(
            {
                "daily": "Проверять: ежедневно",
                "weekly": "Проверять: раз в неделю",
                "manual": "Проверять: вручную",
            }[self.schedule]
        )
        lines.append(f"Максимум за запуск: {self.max_results_per_run}")
        return lines
