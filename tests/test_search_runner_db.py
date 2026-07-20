"""Полный вертикальный сценарий на живой БД.

Запускается только если задан TEST_DATABASE_URL, иначе пропускается:

    docker compose up -d postgres
    TEST_DATABASE_URL=postgresql+asyncpg://bot:bot@localhost:5432/bot pytest tests/test_search_runner_db.py -v
"""

import os
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.db import repositories as repo
from src.db.base import Base
from src.domain.company import CompanyDTO
from src.domain.criteria import SearchCriteria
from src.domain.tax_status import TaxStatus
from src.opendata.enricher import FnsEnricher
from src.providers.fake import FakeProvider
from src.services.search_runner import ItemKind, SearchRunner

TEST_DSN = os.getenv("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(not TEST_DSN, reason="TEST_DATABASE_URL не задан")


@pytest.fixture
async def session():
    engine = create_async_engine(TEST_DSN)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()
    await engine.dispose()


@pytest.fixture
def criteria() -> SearchCriteria:
    return SearchCriteria(
        opf=["ООО"],
        status=["active"],
        regions=["77"],
        reg_date_from=date(2020, 1, 1),
        reg_date_to=date(2025, 12, 31),
        okved_main=["41.20", "41.10", "42.11", "43.11", "43.12", "43.21"],
        financial_year=2025,
        revenue_min=Decimal("5000000"),
        revenue_max=Decimal("500000000"),
        special_tax_regimes="exclude",
        max_results_per_run=50,
    )


@pytest.fixture
async def query(session, criteria):
    user = await repo.get_or_create_user(session, telegram_id=1, username="tester")
    q = await repo.create_query(session, user.id, "Строительные ООО Москвы", criteria)
    await session.commit()
    return q


class TestFnsEnricher:
    """Вывод «вероятная ОСНО» допустим только при полном и свежем индексе."""

    async def _setup(self, session, *, complete: bool, loaded_at=None, regimes=None):
        from datetime import datetime

        from src.db.models import FnsDataset, FnsRecord

        session.add(
            FnsDataset(
                code="snr",
                is_complete=complete,
                loaded_at=loaded_at or datetime.now(),
                records_count=1,
            )
        )
        if regimes is not None:
            session.add(
                FnsRecord(inn="7703023100", dataset_code="snr", name="X", data=regimes)
            )
        await session.commit()

    def _company(self):
        return CompanyDTO(inn="7703023100", ogrn="1027700132195", name="ООО X")

    async def test_absent_inn_with_complete_index_is_probable_osno(self, session):
        await self._setup(session, complete=True)
        company = self._company()
        await FnsEnricher(session).enrich([company])
        assert company.tax_status is TaxStatus.OSNO_PROBABLE

    async def test_present_inn_is_special(self, session):
        await self._setup(session, complete=True, regimes={"usn": True, "ausn": False})
        company = self._company()
        await FnsEnricher(session).enrich([company])
        assert company.tax_status is TaxStatus.SPECIAL
        assert company.tax_regimes == ["УСН"]

    async def test_incomplete_index_yields_unknown(self, session):
        """Главная защита: неполный индекс не даёт права утверждать про ОСНО."""
        await self._setup(session, complete=False)
        company = self._company()
        await FnsEnricher(session).enrich([company])
        assert company.tax_status is TaxStatus.UNKNOWN

    async def test_stale_index_yields_unknown(self, session):
        from datetime import datetime, timedelta

        await self._setup(session, complete=True, loaded_at=datetime.now() - timedelta(days=400))
        company = self._company()
        await FnsEnricher(session).enrich([company])
        assert company.tax_status is TaxStatus.UNKNOWN

    async def test_missing_dataset_yields_unknown(self, session):
        company = self._company()
        await FnsEnricher(session).enrich([company])
        assert company.tax_status is TaxStatus.UNKNOWN

    async def test_confirmed_osno_is_not_downgraded(self, session):
        await self._setup(session, complete=True)
        company = self._company()
        company.tax_status = TaxStatus.OSNO_CONFIRMED
        await FnsEnricher(session).enrich([company])
        assert company.tax_status is TaxStatus.OSNO_CONFIRMED


class TestUserUpsert:
    """Регрессия: aiogram обрабатывает апдейты параллельно, и SELECT-потом-INSERT
    ронял UniqueViolation по users.telegram_id при быстрых повторных сообщениях."""

    async def test_repeated_calls_do_not_violate_unique(self, session):
        first = await repo.get_or_create_user(session, telegram_id=777, username="a")
        second = await repo.get_or_create_user(session, telegram_id=777, username="a")
        await session.commit()
        assert first.id == second.id

    async def test_username_is_updated(self, session):
        await repo.get_or_create_user(session, telegram_id=778, username="old")
        await session.commit()
        user = await repo.get_or_create_user(session, telegram_id=778, username="new")
        await session.commit()
        assert user.username == "new"

    async def test_empty_username_does_not_wipe_stored(self, session):
        await repo.get_or_create_user(session, telegram_id=779, username="keep")
        await session.commit()
        user = await repo.get_or_create_user(session, telegram_id=779, username=None)
        await session.commit()
        assert user.username == "keep"


class TestVerticalScenario:
    async def test_first_run_finds_new_companies(self, session, query):
        runner = SearchRunner(FakeProvider(pool_size=400))
        outcome = await runner.run(session, query)
        await session.commit()

        assert outcome.ok
        assert outcome.received == 400
        assert outcome.matched > 0
        assert outcome.new == outcome.matched
        assert outcome.already_seen == 0
        assert len(outcome.items) == min(outcome.new, 50)

    async def test_second_run_reports_nothing_new(self, session, query):
        """Ключевое требование: повторный запуск не присылает те же компании."""
        runner = SearchRunner(FakeProvider(pool_size=400))
        first = await runner.run(session, query)
        await session.commit()

        second = await runner.run(session, query)
        await session.commit()

        assert second.new == 0
        assert second.changed == 0
        assert second.items == []
        assert second.already_seen == first.matched

    async def test_changed_revenue_is_resent(self, session, query):
        provider = FakeProvider(pool_size=200)
        runner = SearchRunner(provider)
        first = await runner.run(session, query)
        await session.commit()
        assert first.new > 0

        # меняем выручку у первой попавшей в выдачу компании
        target_ogrn = first.items[0].company.ogrn
        for c in provider.pool:
            if c.ogrn == target_ogrn:
                c.revenue = Decimal("123456789")
                break

        second = await runner.run(session, query)
        await session.commit()

        assert second.changed == 1
        assert second.new == 0
        item = second.items[0]
        assert item.kind is ItemKind.CHANGED
        assert "изменилась выручка" in item.reasons

    async def test_run_history_is_recorded(self, session, query):
        runner = SearchRunner(FakeProvider(pool_size=100))
        await runner.run(session, query)
        await session.commit()

        runs = await repo.last_runs(session, query.id)
        assert len(runs) == 1
        assert runs[0].status == "success"
        assert runs[0].received_count == 100
        assert runs[0].finished_at is not None

    async def test_hidden_company_is_not_resent(self, session, query):
        runner = SearchRunner(FakeProvider(pool_size=200))
        first = await runner.run(session, query)
        await session.commit()

        hidden_id = first.items[0].result_id
        await repo.set_result_flag(session, hidden_id, is_hidden=True)
        await session.commit()

        pending = await repo.list_unsent(session, query.id, limit=1000)
        assert hidden_id not in [p.id for p in pending]
