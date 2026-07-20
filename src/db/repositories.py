from datetime import datetime

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.mapping import dto_to_values
from src.db.models import Company, SearchQuery, SearchResult, SearchRun, User
from src.domain.company import CompanyDTO
from src.domain.criteria import SearchCriteria


async def get_or_create_user(session: AsyncSession, telegram_id: int, username: str | None) -> User:
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        user = User(telegram_id=telegram_id, username=username)
        session.add(user)
        await session.flush()
    elif username and user.username != username:
        user.username = username
    return user


# --- поисковые запросы ------------------------------------------------------


async def create_query(
    session: AsyncSession, user_id: int, name: str, criteria: SearchCriteria
) -> SearchQuery:
    query = SearchQuery(
        user_id=user_id,
        name=name,
        criteria_json=criteria.model_dump(mode="json"),
        schedule=criteria.schedule,
    )
    session.add(query)
    await session.flush()
    return query


async def list_queries(session: AsyncSession, user_id: int) -> list[SearchQuery]:
    return list(
        await session.scalars(
            select(SearchQuery).where(SearchQuery.user_id == user_id).order_by(SearchQuery.id)
        )
    )


async def get_query(session: AsyncSession, query_id: int) -> SearchQuery | None:
    return await session.get(SearchQuery, query_id)


async def set_query_active(session: AsyncSession, query_id: int, active: bool) -> None:
    await session.execute(
        update(SearchQuery).where(SearchQuery.id == query_id).values(is_active=active)
    )


async def delete_query(session: AsyncSession, query_id: int) -> None:
    query = await session.get(SearchQuery, query_id)
    if query:
        await session.delete(query)


async def try_lock_query(session: AsyncSession, query_id: int) -> bool:
    """Advisory-lock, чтобы один запрос не запустился дважды (кнопка + планировщик)."""
    result = await session.scalar(text("SELECT pg_try_advisory_lock(:k)").bindparams(k=query_id))
    return bool(result)


async def unlock_query(session: AsyncSession, query_id: int) -> None:
    await session.execute(text("SELECT pg_advisory_unlock(:k)").bindparams(k=query_id))


# --- запуски ----------------------------------------------------------------


async def start_run(session: AsyncSession, query_id: int) -> SearchRun:
    run = SearchRun(search_query_id=query_id, status="running")
    session.add(run)
    await session.flush()
    return run


async def finish_run(session: AsyncSession, run: SearchRun, status: str, error: str | None = None) -> None:
    run.status = status
    run.finished_at = datetime.now()
    run.error_message = error


async def last_runs(session: AsyncSession, query_id: int, limit: int = 5) -> list[SearchRun]:
    return list(
        await session.scalars(
            select(SearchRun)
            .where(SearchRun.search_query_id == query_id)
            .order_by(SearchRun.started_at.desc())
            .limit(limit)
        )
    )


# --- компании ---------------------------------------------------------------


async def upsert_company(session: AsyncSession, dto: CompanyDTO) -> Company:
    """Ключ — ОГРН. Если его нет, запасной вариант — поиск по ИНН."""
    values = dto_to_values(dto)

    if dto.ogrn:
        stmt = (
            insert(Company)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[Company.ogrn],
                set_={k: v for k, v in values.items() if k != "ogrn"},
            )
            .returning(Company)
        )
        return await session.scalar(stmt)

    existing = None
    if dto.inn:
        existing = await session.scalar(select(Company).where(Company.inn == dto.inn))
    if existing:
        for key, value in values.items():
            setattr(existing, key, value)
        return existing

    company = Company(**values)
    session.add(company)
    await session.flush()
    return company


# --- результаты -------------------------------------------------------------


async def get_result(session: AsyncSession, query_id: int, company_id: int) -> SearchResult | None:
    return await session.scalar(
        select(SearchResult).where(
            SearchResult.search_query_id == query_id, SearchResult.company_id == company_id
        )
    )


async def list_results(
    session: AsyncSession, query_id: int, only_favorites: bool = False, limit: int = 100
) -> list[SearchResult]:
    stmt = select(SearchResult).where(
        SearchResult.search_query_id == query_id, SearchResult.is_hidden.is_(False)
    )
    if only_favorites:
        stmt = stmt.where(SearchResult.is_favorite.is_(True))
    return list(await session.scalars(stmt.order_by(SearchResult.first_seen_at.desc()).limit(limit)))


async def count_results(session: AsyncSession, query_id: int) -> int:
    return await session.scalar(
        select(func.count()).select_from(SearchResult).where(SearchResult.search_query_id == query_id)
    ) or 0


async def list_unsent(session: AsyncSession, query_id: int, limit: int) -> list[SearchResult]:
    """Найденные, но ещё не отправленные в чат. Переживает рестарт контейнера."""
    return list(
        await session.scalars(
            select(SearchResult)
            .where(
                SearchResult.search_query_id == query_id,
                SearchResult.last_sent_at.is_(None),
                SearchResult.is_hidden.is_(False),
            )
            .order_by(SearchResult.first_seen_at)
            .limit(limit)
        )
    )


async def count_unsent(session: AsyncSession, query_id: int) -> int:
    return await session.scalar(
        select(func.count())
        .select_from(SearchResult)
        .where(
            SearchResult.search_query_id == query_id,
            SearchResult.last_sent_at.is_(None),
            SearchResult.is_hidden.is_(False),
        )
    ) or 0


async def mark_sent(session: AsyncSession, result_ids: list[int]) -> None:
    if not result_ids:
        return
    await session.execute(
        update(SearchResult)
        .where(SearchResult.id.in_(result_ids))
        .values(last_sent_at=datetime.now())
    )


async def get_result_by_id(session: AsyncSession, result_id: int) -> SearchResult | None:
    return await session.get(SearchResult, result_id)


async def set_result_flag(session: AsyncSession, result_id: int, **flags) -> None:
    await session.execute(update(SearchResult).where(SearchResult.id == result_id).values(**flags))
