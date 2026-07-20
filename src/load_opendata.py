"""Загрузка открытых данных ФНС.

    python -m src.load_opendata           # все подключённые наборы
    python -m src.load_opendata snr       # только указанные

Запускается вручную или по расписанию. Наборы обновляются раз в месяц,
чаще качать бессмысленно.
"""

import asyncio
import sys

import structlog

from src.db.base import session_scope
from src.logging_conf import setup_logging
from src.opendata.datasets import ACTIVE_DATASETS, BY_CODE
from src.opendata.loader import load_dataset

log = structlog.get_logger()


async def main(codes: list[str]) -> int:
    specs = [BY_CODE[c] for c in codes] if codes else list(ACTIVE_DATASETS)

    unknown = [c for c in codes if c not in BY_CODE]
    if unknown:
        print(f"Неизвестные наборы: {', '.join(unknown)}")
        print(f"Доступны: {', '.join(BY_CODE)}")
        return 2

    failed = 0
    for spec in specs:
        print(f"\n=== {spec.title} ({spec.code}) ===", flush=True)
        try:
            async with session_scope() as session:
                state = await load_dataset(session, spec)
            print(f"загружено записей: {state.records_count}, данные на {state.actual_date}")
        except Exception as exc:  # noqa: BLE001 — один упавший набор не должен ронять остальные
            failed += 1
            print(f"ОШИБКА: {type(exc).__name__}: {exc}")

    return 1 if failed else 0


if __name__ == "__main__":
    setup_logging()
    sys.exit(asyncio.run(main(sys.argv[1:])))
