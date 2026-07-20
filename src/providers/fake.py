"""Синтетический провайдер для разработки и тестов.

Это НЕ имитация реального API — он не притворяется внешним источником и явно
называет себя "Тестовые данные" в карточке. Нужен, чтобы прогонять вертикальный
сценарий до получения боевых ключей.
"""

import random
from datetime import date, datetime, timedelta
from decimal import Decimal

from src.domain.company import CompanyDTO
from src.domain.criteria import SearchCriteria
from src.domain.normalize import normalize_inn, normalize_ogrn
from src.domain.tax_status import TaxStatus
from src.providers.base import (
    CompanyProvider,
    ConnectionStatus,
    ProviderCapabilities,
    SearchPage,
)

_NAMES = [
    "СТРОЙМОНТАЖ", "ГЛАВСТРОЙ", "МОСРЕМСТРОЙ", "ТЕХНОСТРОЙ", "ПРОМСТРОЙИНВЕСТ",
    "СТРОЙАЛЬЯНС", "КАПИТАЛСТРОЙ", "ГРАДСТРОЙ", "ЭНЕРГОСТРОЙ", "МЕГАПОЛИС",
    "СТРОЙРЕСУРС", "ВЫСОТА", "ФУНДАМЕНТ", "МОНОЛИТ", "ПРОЕКТСТРОЙ",
]
_OKVEDS = ["41.20", "41.10", "42.11", "43.11", "43.12", "43.21", "46.73", "68.20"]
_REGIONS = {"77": "Москва", "50": "Московская область", "78": "Санкт-Петербург"}


def _inn10(seed: int) -> str:
    body = f"{seed:09d}"[-9:]
    weights = [2, 4, 10, 3, 5, 9, 4, 6, 8]
    ctrl = sum(int(d) * w for d, w in zip(body, weights, strict=True)) % 11 % 10
    return body + str(ctrl)


def _ogrn13(seed: int) -> str:
    body = f"1{seed:011d}"[-12:]
    return body + str(int(body) % 11 % 10)


class FakeProvider(CompanyProvider):
    name = "Тестовые данные"
    capabilities = ProviderCapabilities(
        mass_search=True,
        supported_filters={"regions", "okved", "reg_date", "opf", "status"},
        has_financials=True,
        has_contacts=True,
        has_tax_regime=True,
        rate_limit_rps=100.0,
    )

    def __init__(self, pool_size: int = 400, seed: int = 42):
        self.pool_size = pool_size
        self._seed = seed
        self._pool: list[CompanyDTO] | None = None

    async def check_connection(self) -> ConnectionStatus:
        return ConnectionStatus(ok=True, message="Тестовый провайдер активен")

    def _build_pool(self) -> list[CompanyDTO]:
        rnd = random.Random(self._seed)
        pool = []
        for i in range(self.pool_size):
            region = rnd.choice(list(_REGIONS)) if i % 4 == 0 else "77"
            okved = rnd.choice(_OKVEDS)
            reg_date = date(2018, 1, 1) + timedelta(days=rnd.randint(0, 2900))
            revenue = Decimal(rnd.randrange(500_000, 900_000_000, 100_000))
            has_contacts = rnd.random() < 0.6

            regimes = ["УСН"] if rnd.random() < 0.35 else []
            tax_status = (
                TaxStatus.SPECIAL
                if regimes
                else (TaxStatus.OSNO_PROBABLE if rnd.random() < 0.8 else TaxStatus.UNKNOWN)
            )

            name = f"{rnd.choice(_NAMES)}-{i:03d}"
            pool.append(
                CompanyDTO(
                    inn=normalize_inn(_inn10(int(region) * 10_000_000 + i)),
                    ogrn=normalize_ogrn(_ogrn13(i + 1)),
                    name=f'ООО "{name}"',
                    full_name=f'Общество с ограниченной ответственностью "{name}"',
                    opf="ООО",
                    status="active" if rnd.random() < 0.9 else "liquidating",
                    region_code=region,
                    region_name=_REGIONS[region],
                    registration_date=reg_date,
                    main_okved=okved,
                    okved_list=[okved],
                    revenue_year=2025,
                    revenue=revenue,
                    profit=(revenue * Decimal("0.05")).quantize(Decimal("1")),
                    tax_status=tax_status,
                    tax_regimes=regimes,
                    tax_source=self.name,
                    phones=[f"+7495{rnd.randint(1000000, 9999999)}"] if has_contacts else [],
                    emails=[f"info@{name.lower()}.ru"] if has_contacts and rnd.random() < 0.7 else [],
                    website=f"{name.lower()}.ru" if has_contacts else None,
                    manager_name=f"Иванов И.И.-{i}",
                    source=self.name,
                    source_updated_at=datetime.now(),
                    fields_available={
                        "status", "region_code", "registration_date", "main_okved",
                        "revenue", "profit", "tax_status", "contacts",
                    },
                )
            )
        return pool

    @property
    def pool(self) -> list[CompanyDTO]:
        if self._pool is None:
            self._pool = self._build_pool()
        return self._pool

    async def search(
        self, criteria: SearchCriteria, limit: int, cursor: str | None = None
    ) -> SearchPage:
        offset = int(cursor or 0)
        # провайдер отдаёт всё подряд — фильтрацию делает CriteriaMatcher у нас
        chunk = self.pool[offset : offset + limit]
        next_cursor = str(offset + limit) if offset + limit < len(self.pool) else None
        return SearchPage(items=chunk, next_cursor=next_cursor, total_hint=len(self.pool))

    async def get_company(self, inn_or_ogrn: str) -> CompanyDTO | None:
        for c in self.pool:
            if inn_or_ogrn in (c.inn, c.ogrn):
                return c
        return None

    def normalize_response(self, response: dict) -> CompanyDTO:
        return CompanyDTO(**response)
