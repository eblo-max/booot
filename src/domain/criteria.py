from datetime import date, timedelta
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
    # относительное окно: "за последние N дней", пересчитывается на каждом запуске.
    # Имеет приоритет над абсолютными датами — иначе сохранённый запрос протухает.
    reg_last_days: int | None = None

    okved_main: list[str] = Field(default_factory=list)
    okved_match_mode: Literal["main_only", "main_or_additional"] = "main_only"

    financial_year: int | None = None
    revenue_min: Decimal | None = None
    revenue_max: Decimal | None = None
    profit_min: Decimal | None = None

    contacts_required: Literal["no", "preferred", "required"] = "preferred"
    # точечные требования поверх contacts_required: обзвон и рассылка нужны разные
    require_phone: bool = False
    require_email: bool = False
    require_website: bool = False
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

    def effective_reg_range(self, today: date | None = None) -> tuple[date | None, date | None]:
        """Границы периода регистрации на момент запуска.

        Относительное окно всегда считается от текущей даты, поэтому ежедневный
        мониторинг «зарегистрированы за последние N дней» не устаревает.
        """
        if self.reg_last_days is not None:
            today = today or date.today()
            return today - timedelta(days=self.reg_last_days), today
        return self.reg_date_from, self.reg_date_to

    def reg_period_text(self) -> str | None:
        if self.reg_last_days is not None:
            return f"Регистрация: за последние {self.reg_last_days} дн."
        if self.reg_date_from or self.reg_date_to:
            frm = self.reg_date_from.strftime("%d.%m.%Y") if self.reg_date_from else "—"
            to = self.reg_date_to.strftime("%d.%m.%Y") if self.reg_date_to else "—"
            return f"Регистрация: {frm} — {to}"
        return None

    def contacts_text(self) -> str:
        required = []
        if self.require_phone:
            required.append("телефон")
        if self.require_email:
            required.append("e-mail")
        if self.require_website:
            required.append("сайт")
        if required:
            return "Контакты обязательно: " + ", ".join(required)
        return {
            "no": "Контакты: не важно",
            "preferred": "Контакты: желательно",
            "required": "Контакты: обязателен любой",
        }[self.contacts_required]

    def summary_lines(self) -> list[str]:
        """Сводка критериев для экрана подтверждения."""
        lines = [
            f"ОПФ: {', '.join(self.opf) if self.opf else 'любая'}",
            f"Статус: {'действующие' if self.status == ['active'] else ', '.join(self.status)}",
            f"Регионы: {', '.join(self.regions) if self.regions else 'вся РФ'}",
        ]
        period = self.reg_period_text()
        if period:
            lines.append(period)
        if self.okved_main:
            mode = "основной" if self.okved_match_mode == "main_only" else "основной или доп."
            lines.append(f"ОКВЭД ({mode}): {', '.join(self.okved_main)}")
        if self.revenue_min is not None or self.revenue_max is not None:
            frm = f"{self.revenue_min:,.0f}".replace(",", " ") if self.revenue_min else "0"
            to = f"{self.revenue_max:,.0f}".replace(",", " ") if self.revenue_max else "∞"
            year = f" за {self.financial_year}" if self.financial_year else ""
            lines.append(f"Выручка{year}: {frm} — {to} ₽")
        if self.profit_min is not None:
            lines.append(f"Прибыль от: {self.profit_min:,.0f}".replace(",", " ") + " ₽")
        lines.append(self.contacts_text())
        lines.append(
            {
                "exclude": "Спецрежимы: исключить",
                "allow": "Спецрежимы: допустимы",
                "only": "Спецрежимы: только они",
            }[self.special_tax_regimes]
        )
        if self.special_tax_regimes != "allow":
            lines.append(
                "Режим неизвестен: "
                + ("пускать" if self.allow_unknown_tax_status else "отсеивать")
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
