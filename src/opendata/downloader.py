"""Поиск и скачивание актуальной выгрузки ФНС.

Имя файла содержит дату публикации и версию структуры и меняется при каждом
обновлении, поэтому ссылку берём со страницы набора, а не хардкодим.
"""

import re
from dataclasses import dataclass
from pathlib import Path

import httpx
import structlog

from src.opendata.datasets import DatasetSpec

log = structlog.get_logger()

DATASET_PAGE = "https://www.nalog.gov.ru/opendata/{slug}/"
_ZIP_LINK = re.compile(r'href="([^"]*?/opendata/[^"]*?data-(\d{8})-structure-[^"]*?\.zip)"', re.I)


@dataclass
class RemoteFile:
    url: str
    published: str  # YYYYMMDD из имени файла
    file_id: str


class DatasetNotFound(RuntimeError):
    pass


async def resolve_latest(spec: DatasetSpec, client: httpx.AsyncClient) -> RemoteFile:
    """Находит ссылку на самую свежую выгрузку на странице набора."""
    page_url = DATASET_PAGE.format(slug=spec.slug)
    response = await client.get(page_url, timeout=60)
    response.raise_for_status()

    matches = _ZIP_LINK.findall(response.text)
    if not matches:
        raise DatasetNotFound(
            f"На странице набора {spec.code} не нашлась ссылка на zip-выгрузку. "
            "Вероятно, ФНС изменила вёрстку страницы."
        )

    href, published = max(matches, key=lambda m: m[1])
    url = href if href.startswith("http") else "https://file.nalog.ru" + href[href.find("/opendata") :]
    return RemoteFile(url=url, published=published, file_id=url.rsplit("/", 1)[-1])


async def download(url: str, target: Path, client: httpx.AsyncClient) -> Path:
    """Качает архив потоково — файлы до сотен мегабайт."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")

    async with client.stream("GET", url, timeout=600, follow_redirects=True) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length") or 0)
        done = 0
        with tmp.open("wb") as handle:
            async for chunk in response.aiter_bytes(1 << 20):
                handle.write(chunk)
                done += len(chunk)
                if total and done % (50 << 20) < (1 << 20):
                    log.info("download_progress", url=url, mb_done=done >> 20, mb_total=total >> 20)

    tmp.replace(target)
    log.info("download_finished", url=url, mb=target.stat().st_size >> 20)
    return target
