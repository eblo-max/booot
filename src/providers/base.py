from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from src.domain.company import CompanyDTO
from src.domain.criteria import SearchCriteria


@dataclass
class ProviderCapabilities:
    mass_search: bool = False
    supported_filters: set[str] = field(default_factory=set)
    has_financials: bool = False
    has_contacts: bool = False
    has_tax_regime: bool = False
    rate_limit_rps: float = 1.0
    daily_quota: int | None = None


@dataclass
class ConnectionStatus:
    ok: bool
    message: str
    quota_left: int | None = None


@dataclass
class SearchPage:
    items: list[CompanyDTO]
    next_cursor: str | None = None
    total_hint: int | None = None


class CompanyProvider(ABC):
    """Единый интерфейс источника данных.

    Провайдеры делятся на два типа:
      - mass_search=True  — умеет отдавать поток компаний по критериям (search)
      - mass_search=False — только точечное обогащение (get_company)
    """

    name: str = "base"
    capabilities: ProviderCapabilities = ProviderCapabilities()

    @abstractmethod
    async def check_connection(self) -> ConnectionStatus: ...

    @abstractmethod
    async def search(
        self, criteria: SearchCriteria, limit: int, cursor: str | None = None
    ) -> SearchPage:
        """Поднимает MassSearchNotSupported, если источник так не умеет."""

    @abstractmethod
    async def get_company(self, inn_or_ogrn: str) -> CompanyDTO | None: ...

    @abstractmethod
    def normalize_response(self, response: dict) -> CompanyDTO: ...

    async def close(self) -> None:
        """Освободить http-соединения."""
        return None

    def unsupported_filters(self, criteria: SearchCriteria) -> list[str]:
        """Какие критерии придётся применять на нашей стороне."""
        used = set()
        if criteria.regions:
            used.add("regions")
        if criteria.okved_main:
            used.add("okved")
        if criteria.reg_date_from or criteria.reg_date_to:
            used.add("reg_date")
        if criteria.revenue_min is not None or criteria.revenue_max is not None:
            used.add("revenue")
        if criteria.contacts_required != "no":
            used.add("contacts")
        if criteria.special_tax_regimes != "allow":
            used.add("tax_regime")
        if criteria.opf:
            used.add("opf")
        if criteria.status:
            used.add("status")
        return sorted(used - self.capabilities.supported_filters)
