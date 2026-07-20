"""Проверка связки провайдер → нормализация → дедупликация → фильтрация.

БД здесь не участвует: это тот же путь, что проходит SearchRunner до записи
результатов, но без Postgres.
"""

from datetime import date
from decimal import Decimal

import pytest

from src.domain.criteria import SearchCriteria
from src.domain.matcher import CriteriaMatcher
from src.domain.normalize import normalize_inn, normalize_ogrn, okved_matches
from src.domain.tax_status import TaxStatus
from src.providers.fake import FakeProvider


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
        contacts_required="preferred",
        special_tax_regimes="exclude",
        max_results_per_run=50,
    )


async def collect(provider: FakeProvider, criteria: SearchCriteria):
    """Повторяет _fetch из SearchRunner."""
    seen, collected, cursor = set(), [], None
    while True:
        page = await provider.search(criteria, limit=100, cursor=cursor)
        for dto in page.items:
            dto.inn = normalize_inn(dto.inn)
            dto.ogrn = normalize_ogrn(dto.ogrn)
            if not dto.key or dto.key in seen:
                continue
            seen.add(dto.key)
            collected.append(dto)
        cursor = page.next_cursor
        if not cursor:
            break
    return collected


class TestPipeline:
    async def test_provider_returns_full_pool(self, criteria):
        provider = FakeProvider(pool_size=400)
        collected = await collect(provider, criteria)
        assert len(collected) == 400

    async def test_all_identifiers_valid_after_normalization(self, criteria):
        collected = await collect(FakeProvider(pool_size=200), criteria)
        assert all(c.ogrn and normalize_ogrn(c.ogrn) for c in collected)
        assert all(c.inn is None or normalize_inn(c.inn) for c in collected)

    async def test_no_duplicates_by_ogrn(self, criteria):
        collected = await collect(FakeProvider(pool_size=300), criteria)
        keys = [c.key for c in collected]
        assert len(keys) == len(set(keys))

    async def test_filtering_narrows_the_pool(self, criteria):
        collected = await collect(FakeProvider(pool_size=400), criteria)
        matcher = CriteriaMatcher(criteria)
        matched = [c for c in collected if matcher.match(c)]

        assert 0 < len(matched) < len(collected), "фильтр должен отсекать часть пула"

    async def test_every_match_satisfies_every_criterion(self, criteria):
        collected = await collect(FakeProvider(pool_size=400), criteria)
        matcher = CriteriaMatcher(criteria)

        for c in (x for x in collected if matcher.match(x)):
            assert c.region_code == "77"
            assert c.status == "active"
            assert criteria.reg_date_from <= c.registration_date <= criteria.reg_date_to
            assert okved_matches(c.main_okved, criteria.okved_main)
            assert criteria.revenue_min <= c.revenue <= criteria.revenue_max
            assert c.tax_status is not TaxStatus.SPECIAL

    async def test_second_run_yields_identical_set(self, criteria):
        """Дедупликация по ОГРН стабильна между запусками — основа для «только новые»."""
        first = {c.key for c in await collect(FakeProvider(pool_size=250), criteria)}
        second = {c.key for c in await collect(FakeProvider(pool_size=250), criteria)}
        assert first == second

    async def test_pagination_covers_pool_without_gaps(self, criteria):
        provider = FakeProvider(pool_size=250)
        collected = await collect(provider, criteria)
        assert {c.key for c in collected} == {c.ogrn for c in provider.pool}


class TestProviderContract:
    async def test_check_connection(self):
        assert (await FakeProvider().check_connection()).ok

    async def test_get_company_by_ogrn(self):
        provider = FakeProvider(pool_size=50)
        target = provider.pool[7]
        assert (await provider.get_company(target.ogrn)).ogrn == target.ogrn

    async def test_get_company_missing(self):
        assert await FakeProvider(pool_size=10).get_company("1027700132195") is None

    def test_declares_mass_search(self):
        assert FakeProvider().capabilities.mass_search is True
