"""Ядро запуска поиска. Не знает про Telegram — возвращает результат, а не шлёт его."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import repositories as repo
from src.db.models import SearchQuery, SearchRun
from src.domain.company import CompanyDTO
from src.domain.criteria import SearchCriteria
from src.domain.hashing import data_hash, diff_reasons, significant_snapshot
from src.domain.matcher import CriteriaMatcher
from src.domain.normalize import normalize_inn, normalize_ogrn
from src.domain.tax_status import TaxStatus
from src.providers.base import CompanyProvider
from src.providers.exceptions import (
    MassSearchNotSupported,
    ProviderAuthError,
    ProviderError,
    ProviderQuotaExceeded,
    ProviderRateLimited,
)

log = structlog.get_logger()

MAX_PAGES = 200


class ItemKind(StrEnum):
    NEW = "new"
    CHANGED = "changed"


@dataclass
class DeliveryItem:
    company: CompanyDTO
    result_id: int
    kind: ItemKind
    reasons: list[str] = field(default_factory=list)


@dataclass
class RunOutcome:
    run_id: int
    query_id: int
    query_name: str
    received: int = 0
    matched: int = 0
    already_seen: int = 0
    new: int = 0
    changed: int = 0
    with_contacts: int = 0
    probable_osno: int = 0
    unknown_tax: int = 0
    items: list[DeliveryItem] = field(default_factory=list)
    error: str | None = None
    notice: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class SearchRunner:
    def __init__(self, provider: CompanyProvider):
        self.provider = provider

    async def run(self, session: AsyncSession, query: SearchQuery) -> RunOutcome:
        criteria = SearchCriteria.model_validate(query.criteria_json)
        run = await repo.start_run(session, query.id)
        outcome = RunOutcome(run_id=run.id, query_id=query.id, query_name=query.name)

        try:
            companies = await self._fetch(criteria, outcome)
        except MassSearchNotSupported as exc:
            return await self._fail(
                session, run, outcome, str(exc),
                notice=(
                    f"Источник «{self.provider.name}» не поддерживает массовый поиск по фильтрам. "
                    "Загрузите список компаний через /upload — критерии применю к файлу."
                ),
            )
        except ProviderAuthError as exc:
            return await self._fail(session, run, outcome, f"Ошибка авторизации: {exc}")
        except ProviderQuotaExceeded as exc:
            return await self._fail(session, run, outcome, f"Исчерпан лимит тарифа: {exc}")
        except ProviderRateLimited as exc:
            return await self._fail(session, run, outcome, f"Превышен rate limit: {exc}")
        except ProviderError as exc:
            return await self._fail(session, run, outcome, f"Источник недоступен: {exc}")

        await self._reconcile(session, query, run, criteria, companies, outcome)

        run.received_count = outcome.received
        run.matched_count = outcome.matched
        run.new_count = outcome.new
        run.changed_count = outcome.changed
        await repo.finish_run(session, run, status="success")

        query.last_run_at = datetime.now()
        log.info(
            "search_run_finished",
            query_id=query.id,
            received=outcome.received,
            matched=outcome.matched,
            new=outcome.new,
            changed=outcome.changed,
        )
        return outcome

    # --- шаги ---------------------------------------------------------------

    async def _fetch(self, criteria: SearchCriteria, outcome: RunOutcome) -> list[CompanyDTO]:
        """Постранично тянет источник, нормализует реквизиты, дедуплицирует батч."""
        if not self.provider.capabilities.mass_search:
            raise MassSearchNotSupported(f"{self.provider.name} умеет только точечные запросы")

        unsupported = self.provider.unsupported_filters(criteria)
        if unsupported:
            log.info("filters_applied_locally", provider=self.provider.name, filters=unsupported)

        seen_keys: set[str] = set()
        collected: list[CompanyDTO] = []
        cursor: str | None = None

        for _ in range(MAX_PAGES):
            page = await self.provider.search(criteria, limit=100, cursor=cursor)
            outcome.received += len(page.items)

            for dto in page.items:
                normalized = self._normalize(dto)
                if not normalized.key or normalized.key in seen_keys:
                    continue
                seen_keys.add(normalized.key)
                collected.append(normalized)

            cursor = page.next_cursor
            if not cursor or not page.items:
                break

        return collected

    def _normalize(self, dto: CompanyDTO) -> CompanyDTO:
        """ИНН/ОГРН с невалидной контрольной суммой обнуляются, но компания не выбрасывается."""
        dto.inn = normalize_inn(dto.inn)
        dto.ogrn = normalize_ogrn(dto.ogrn)
        if not dto.region_code and dto.inn:
            dto.region_code = dto.inn[:2]
        return dto

    async def _reconcile(
        self,
        session: AsyncSession,
        query: SearchQuery,
        run: SearchRun,
        criteria: SearchCriteria,
        companies: list[CompanyDTO],
        outcome: RunOutcome,
    ) -> None:
        matcher = CriteriaMatcher(criteria)
        now = datetime.now()

        for dto in companies:
            if not matcher.match(dto):
                continue

            outcome.matched += 1
            if dto.has_contacts:
                outcome.with_contacts += 1
            if dto.tax_status == TaxStatus.OSNO_PROBABLE:
                outcome.probable_osno += 1
            elif dto.tax_status == TaxStatus.UNKNOWN:
                outcome.unknown_tax += 1

            company = await repo.upsert_company(session, dto)
            existing = await repo.get_result(session, query.id, company.id)
            snapshot = significant_snapshot(dto)
            new_hash = data_hash(dto)

            if existing is None:
                result = await self._insert_result(session, query, run, company.id, new_hash, snapshot)
                outcome.new += 1
                if len(outcome.items) < criteria.max_results_per_run:
                    outcome.items.append(
                        DeliveryItem(company=dto, result_id=result.id, kind=ItemKind.NEW)
                    )
                continue

            existing.last_seen_at = now
            existing.search_run_id = run.id

            if existing.data_hash == new_hash:
                outcome.already_seen += 1
                continue

            reasons = diff_reasons(existing.snapshot_json, snapshot)
            existing.data_hash = new_hash
            existing.snapshot_json = snapshot
            existing.change_reason = reasons

            if not reasons or existing.is_hidden:
                # хеш поменялся, но повода беспокоить пользователя нет
                outcome.already_seen += 1
                continue

            outcome.changed += 1
            if len(outcome.items) < criteria.max_results_per_run:
                outcome.items.append(
                    DeliveryItem(
                        company=dto, result_id=existing.id, kind=ItemKind.CHANGED, reasons=reasons
                    )
                )

    async def _insert_result(self, session, query, run, company_id: int, new_hash: str, snapshot: dict):
        from src.db.models import SearchResult

        result = SearchResult(
            search_query_id=query.id,
            search_run_id=run.id,
            company_id=company_id,
            data_hash=new_hash,
            snapshot_json=snapshot,
        )
        session.add(result)
        await session.flush()
        return result

    async def _fail(
        self,
        session: AsyncSession,
        run: SearchRun,
        outcome: RunOutcome,
        error: str,
        notice: str | None = None,
    ) -> RunOutcome:
        outcome.error = error
        outcome.notice = notice
        await repo.finish_run(session, run, status="failed", error=error)
        log.warning("search_run_failed", run_id=run.id, error=error)
        return outcome
