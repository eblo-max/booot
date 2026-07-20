from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from src.domain.tax_status import TaxStatus


class CompanyDTO(BaseModel):
    """Нормализованная компания от любого провайдера.

    fields_available отличает "значение отсутствует в ответе" от "значение равно нулю".
    Без этого matcher молча отсеивал бы компании по данным, которых источник не давал.
    """

    inn: str | None = None
    ogrn: str | None = None
    name: str = ""
    full_name: str | None = None
    opf: str | None = None
    status: str | None = None
    region_code: str | None = None
    region_name: str | None = None
    registration_date: date | None = None
    main_okved: str | None = None
    okved_list: list[str] = Field(default_factory=list)

    revenue_year: int | None = None
    revenue: Decimal | None = None
    profit: Decimal | None = None

    tax_status: TaxStatus = TaxStatus.UNKNOWN
    tax_regimes: list[str] = Field(default_factory=list)
    tax_source: str | None = None

    phones: list[str] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)
    website: str | None = None
    manager_name: str | None = None

    source: str = ""
    source_updated_at: datetime | None = None
    raw: dict = Field(default_factory=dict)

    fields_available: set[str] = Field(default_factory=set)

    @property
    def key(self) -> str:
        """Главный идентификатор — ОГРН, запасной — ИНН."""
        return self.ogrn or self.inn or ""

    @property
    def has_contacts(self) -> bool:
        return bool(self.phones or self.emails)

    def knows(self, field: str) -> bool:
        return field in self.fields_available
