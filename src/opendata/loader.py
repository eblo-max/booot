"""Загрузка выгрузок ФНС в нашу базу."""

import tempfile
from datetime import datetime
from pathlib import Path

import httpx
import structlog
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import FnsDataset, FnsRecord
from src.opendata.datasets import DatasetSpec
from src.opendata.downloader import download, resolve_latest
from src.opendata.parser import parse_archive

log = structlog.get_logger()

BATCH = 5_000


async def load_dataset(
    session: AsyncSession, spec: DatasetSpec, workdir: Path | None = None
) -> FnsDataset:
    """Скачивает и загружает набор целиком.

    Пометка is_complete ставится только после успешного прохода всего архива:
    на неё опирается вывод об отсутствии спецрежима.
    """
    workdir = workdir or Path(tempfile.gettempdir()) / "fns"
    state = await session.get(FnsDataset, spec.code) or FnsDataset(code=spec.code)
    state.is_complete = False
    state.error_message = None
    session.add(state)
    await session.flush()

    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            remote = await resolve_latest(spec, client)
            log.info("dataset_resolved", dataset=spec.code, file=remote.file_id)
            archive = await download(remote.url, workdir / f"{spec.code}.zip", client)

        state.file_id = remote.file_id
        count = await _ingest(session, spec, archive)

        state.records_count = count
        state.actual_date = await _peek_actual_date(session, spec)
        state.is_complete = True
        state.loaded_at = datetime.now()
        log.info("dataset_loaded", dataset=spec.code, records=count)
    except Exception as exc:  # noqa: BLE001 — состояние набора важнее падения задачи
        state.error_message = f"{type(exc).__name__}: {exc}"
        log.warning("dataset_load_failed", dataset=spec.code, error=str(exc))
        raise
    finally:
        await session.flush()

    return state


async def _ingest(session: AsyncSession, spec: DatasetSpec, archive: Path) -> int:
    batch: list[dict] = []
    total = 0

    for record in parse_archive(spec, archive):
        batch.append(
            {
                "inn": record.inn,
                "dataset_code": spec.code,
                "name": record.name,
                "data": record.data,
                "actual_date": record.actual_date,
            }
        )
        if len(batch) >= BATCH:
            total += await _flush(session, batch)
            batch = []

    if batch:
        total += await _flush(session, batch)
    return total


async def _flush(session: AsyncSession, rows: list[dict]) -> int:
    stmt = insert(FnsRecord).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_fns_inn_dataset",
        set_={
            "name": stmt.excluded.name,
            "data": stmt.excluded.data,
            "actual_date": stmt.excluded.actual_date,
            "updated_at": datetime.now(),
        },
    )
    await session.execute(stmt)
    await session.commit()
    return len(rows)


async def _peek_actual_date(session: AsyncSession, spec: DatasetSpec):
    from sqlalchemy import func, select

    return await session.scalar(
        select(func.max(FnsRecord.actual_date)).where(FnsRecord.dataset_code == spec.code)
    )
