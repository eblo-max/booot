"""Обогащение компаний данными ФНС из локального индекса.

Здесь налоговый режим впервые получает настоящее основание: набор «Спецрежимы»
перечисляет только тех, кто их применяет, поэтому отсутствие ИНН — значимый факт.
Но только при полностью загруженном наборе, иначе это «мы не знаем».
"""

from datetime import date, timedelta
from decimal import Decimal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import FnsDataset, FnsRecord
from src.domain.company import CompanyDTO
from src.domain.tax_status import TaxStatus, resolve_tax_status
from src.opendata.datasets import REVEXP, SNR

log = structlog.get_logger()

SOURCE_NAME = "Открытые данные ФНС"
# набор обновляется ежемесячно; после этого срока считаем его протухшим
MAX_AGE = timedelta(days=120)

_REGIME_LABELS = {"usn": "УСН", "ausn": "АУСН", "eshn": "ЕСХН", "srp": "СРП"}


class FnsEnricher:
    def __init__(self, session: AsyncSession):
        self.session = session
        self._usable: dict[str, bool] = {}

    async def _is_usable(self, code: str) -> bool:
        """Набор пригоден, если загружен целиком и не устарел."""
        if code in self._usable:
            return self._usable[code]

        state = await self.session.get(FnsDataset, code)
        usable = bool(
            state
            and state.is_complete
            and state.loaded_at
            and (date.today() - state.loaded_at.date()) <= MAX_AGE
        )
        if state and not usable:
            log.info(
                "dataset_not_usable",
                dataset=code,
                complete=state.is_complete,
                loaded_at=str(state.loaded_at),
            )
        self._usable[code] = usable
        return usable

    async def _fetch(self, code: str, inns: list[str]) -> dict[str, FnsRecord]:
        if not inns:
            return {}
        rows = await self.session.scalars(
            select(FnsRecord).where(
                FnsRecord.dataset_code == code, FnsRecord.inn.in_(inns)
            )
        )
        return {row.inn: row for row in rows}

    async def enrich(self, companies: list[CompanyDTO]) -> None:
        """Дополняет компании на месте. Данные источника не перетираются:
        то, что уже пришло от платного API, считается более свежим."""
        inns = [c.inn for c in companies if c.inn]
        if not inns:
            return

        snr_usable = await self._is_usable(SNR.code)
        revexp_usable = await self._is_usable(REVEXP.code)

        snr_rows = await self._fetch(SNR.code, inns) if snr_usable else {}
        revexp_rows = await self._fetch(REVEXP.code, inns) if revexp_usable else {}

        for company in companies:
            if not company.inn:
                continue
            if snr_usable:
                self._apply_tax(company, snr_rows.get(company.inn))
            if revexp_usable and company.revenue is None:
                self._apply_revenue(company, revexp_rows.get(company.inn))

    def _apply_tax(self, company: CompanyDTO, row: FnsRecord | None) -> None:
        # режим, полученный из более надёжного источника, не трогаем
        if company.tax_status is TaxStatus.OSNO_CONFIRMED:
            return

        regimes = []
        if row is not None:
            regimes = [label for key, label in _REGIME_LABELS.items() if row.data.get(key)]

        company.tax_status = resolve_tax_status(
            registry_checked=True,  # набор загружен целиком и свеж — проверка состоялась
            found_regimes=regimes,
        )
        company.tax_regimes = regimes
        company.tax_source = SOURCE_NAME
        company.fields_available.add("tax_status")

    def _apply_revenue(self, company: CompanyDTO, row: FnsRecord | None) -> None:
        if row is None:
            return
        raw = row.data.get("revenue")
        if raw is None:
            return
        company.revenue = Decimal(str(raw))
        if row.data.get("expenses") is not None:
            # в наборе только доходы и расходы; прибыль отсюда — оценка, не отчётная
            company.profit = company.revenue - Decimal(str(row.data["expenses"]))
        if row.actual_date:
            # данные набора относятся к году, предшествующему публикации
            company.revenue_year = row.actual_date.year - 1
        company.source = company.source or SOURCE_NAME
        company.fields_available.add("revenue")
