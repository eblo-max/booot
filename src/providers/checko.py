"""Checko (API-домен ofdata.ru).

Контракт по официальной документации:
  GET /v2/search?key=…&by=okved&obj=org&query=41.20&region=77&active=true&limit=100&page=1
  GET /v2/company?key=…&inn=…
  GET /v2/finances?key=…&inn=…

Поиск отдаёт только регистрационные данные: контактов и финансов там нет,
поэтому за ними идёт отдельный запрос на компанию. Это и есть основной
расход лимита — 100 бесплатных запросов в сутки уходят быстро.
"""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import httpx
import structlog

from src.domain.company import CompanyDTO
from src.domain.criteria import SearchCriteria
from src.domain.normalize import (
    normalize_emails,
    normalize_inn,
    normalize_ogrn,
    normalize_okved,
    normalize_phones,
    normalize_website,
)
from src.domain.tax_status import TaxStatus, resolve_tax_status
from src.providers.base import (
    CompanyProvider,
    ConnectionStatus,
    ProviderCapabilities,
    SearchPage,
)
from src.providers.exceptions import (
    MassSearchNotSupported,
    ProviderAuthError,
    ProviderQuotaExceeded,
    ProviderRateLimited,
    ProviderUnavailable,
)

log = structlog.get_logger()

BASE_URL = "https://api.ofdata.ru/v2"

_SPECIAL_REGIMES = {"УСН", "АУСН", "ЕСХН", "ПСН", "НПД", "ЕНВД", "СРП"}


def _decimal(raw) -> Decimal | None:
    if raw in (None, ""):
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, TypeError):
        return None


def _date(raw: str | None) -> date | None:
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw[:10], fmt).date()
        except ValueError:
            continue
    return None


class CheckoProvider(CompanyProvider):
    name = "Checko"
    capabilities = ProviderCapabilities(
        mass_search=True,
        # выручку, контакты и режим Checko фильтровать не умеет — это делаем мы
        supported_filters={"regions", "okved", "status"},
        has_financials=True,
        has_contacts=True,
        has_tax_regime=True,
        rate_limit_rps=2.0,
        daily_quota=100,  # бесплатный лимит; сверх него запросы платные
    )

    def __init__(self, api_key: str, fetch_details: bool = True):
        self.api_key = api_key
        # без деталей поиск дешевле, но остаётся без контактов и финансов
        self.fetch_details = fetch_details
        self._client = httpx.AsyncClient(timeout=60, follow_redirects=True)
        self.requests_today: int | None = None
        self.balance: float | None = None

    async def close(self) -> None:
        await self._client.aclose()

    # --- транспорт ---------------------------------------------------------

    async def _get(self, path: str, params: dict) -> dict:
        try:
            response = await self._client.get(
                f"{BASE_URL}{path}", params={**params, "key": self.api_key}
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(str(exc)) from exc

        if response.status_code == 429:
            raise ProviderRateLimited("Checko: 429 Too Many Requests")

        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderUnavailable(
                f"Checko вернул не JSON (HTTP {response.status_code}): {response.text[:200]}"
            ) from exc

        meta = payload.get("meta") or {}
        self.requests_today = meta.get("today_request_count", self.requests_today)
        self.balance = meta.get("balance", self.balance)

        if meta.get("status") == "error" or response.status_code >= 400:
            message = meta.get("message") or f"HTTP {response.status_code}"
            lowered = str(message).lower()
            if "ключ" in lowered or response.status_code in (401, 403):
                raise ProviderAuthError(f"Checko: {message}")
            if "лимит" in lowered or "баланс" in lowered:
                raise ProviderQuotaExceeded(f"Checko: {message}")
            raise ProviderUnavailable(f"Checko: {message}")

        return payload

    # --- интерфейс ---------------------------------------------------------

    async def check_connection(self) -> ConnectionStatus:
        try:
            await self._get("/company", {"inn": "7707083893"})
        except ProviderAuthError as exc:
            return ConnectionStatus(ok=False, message=str(exc))
        except Exception as exc:  # noqa: BLE001
            return ConnectionStatus(ok=False, message=str(exc))

        left = None
        if self.requests_today is not None:
            left = max(0, (self.capabilities.daily_quota or 0) - self.requests_today)
        return ConnectionStatus(
            ok=True,
            message=f"запросов сегодня: {self.requests_today}, баланс: {self.balance}",
            quota_left=left,
        )

    async def get_company(self, inn_or_ogrn: str) -> CompanyDTO | None:
        key = "ogrn" if len(inn_or_ogrn.strip()) in (13, 15) else "inn"
        payload = await self._get("/company", {key: inn_or_ogrn.strip()})
        if not payload.get("data"):
            return None
        return self.normalize_response(payload["data"])

    async def search(
        self, criteria: SearchCriteria, limit: int, cursor: str | None = None
    ) -> SearchPage:
        """Ищет по ОКВЭД: у Checko это единственный способ получить отраслевой список.

        Курсор — «индекс кода ОКВЭД : номер страницы», потому что по каждому коду
        приходится идти отдельным запросом.
        """
        if not criteria.okved_main:
            raise MassSearchNotSupported(
                "Checko ищет списком только по коду ОКВЭД. Укажите хотя бы один код "
                "в критериях запроса или загрузите список через /upload."
            )

        okved_index, page = self._parse_cursor(cursor)
        if okved_index >= len(criteria.okved_main):
            return SearchPage(items=[], next_cursor=None)

        code = criteria.okved_main[okved_index]
        params = {
            "by": "okved",
            "obj": "org",
            "query": code,
            "limit": min(limit, 100),
            "page": page,
        }
        if criteria.regions:
            # API принимает один код региона за запрос
            params["region"] = criteria.regions[0]
        if criteria.status == ["active"]:
            params["active"] = "true"

        payload = await self._get("/search", params)
        data = payload.get("data") or {}
        records = data.get("Записи") or []
        items = [self.normalize_response(record) for record in records]

        if self.fetch_details:
            items = await self._enrich_details(items)

        total_pages = int(data.get("СтрВсего") or 1)
        if page < total_pages:
            next_cursor = f"{okved_index}:{page + 1}"
        elif okved_index + 1 < len(criteria.okved_main):
            next_cursor = f"{okved_index + 1}:1"
        else:
            next_cursor = None

        return SearchPage(items=items, next_cursor=next_cursor)

    def _parse_cursor(self, cursor: str | None) -> tuple[int, int]:
        if not cursor:
            return 0, 1
        okved_index, _, page = cursor.partition(":")
        return int(okved_index or 0), int(page or 1)

    async def _enrich_details(self, items: list[CompanyDTO]) -> list[CompanyDTO]:
        """Дотягивает контакты и налоговый режим по каждой компании.

        Дорого по лимиту: один запрос на компанию. Ошибка по одной компании
        не должна ронять весь прогон.
        """
        enriched = []
        for item in items:
            key = item.inn or item.ogrn
            if not key:
                enriched.append(item)
                continue
            try:
                detailed = await self.get_company(key)
            except ProviderQuotaExceeded:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("checko_detail_failed", inn=key, error=str(exc))
                enriched.append(item)
                continue
            enriched.append(detailed or item)
        return enriched

    # --- разбор ------------------------------------------------------------

    def normalize_response(self, response: dict) -> CompanyDTO:
        status_block = response.get("Статус")
        region_block = response.get("Регион")
        okved_block = response.get("ОКВЭД")
        contacts = response.get("Контакты") or {}

        dto = CompanyDTO(
            inn=normalize_inn(response.get("ИНН")),
            ogrn=normalize_ogrn(response.get("ОГРН")),
            name=response.get("НаимСокр") or response.get("НаимПолн") or "",
            full_name=response.get("НаимПолн"),
            opf=self._opf(response.get("НаимСокр") or response.get("НаимПолн") or ""),
            status=self._status(status_block, response.get("ДатаЛикв")),
            region_code=self._region_code(region_block),
            region_name=self._region_name(region_block),
            registration_date=_date(response.get("ДатаРег")),
            main_okved=normalize_okved(self._okved_code(okved_block)),
            okved_list=self._okved_list(response),
            manager_name=self._manager(response.get("Руковод")),
            phones=normalize_phones(contacts.get("Тел")),
            emails=normalize_emails(contacts.get("Емэйл")),
            website=normalize_website(contacts.get("ВебСайт")),
            source=self.name,
            raw=response,
        )
        self._apply_tax(dto, response.get("Налоги") or {})
        self._mark_available(dto, response)
        return dto

    def _opf(self, name: str) -> str | None:
        upper = name.upper()
        for code in ("ПАО", "НАО", "ООО", "АО", "ИП"):
            if code in upper:
                return code
        return None

    def _status(self, block, liquidation_date) -> str | None:
        if liquidation_date:
            return "liquidated"
        if block is None:
            return None
        text = (block.get("Наим") if isinstance(block, dict) else str(block)) or ""
        lowered = text.lower()
        if "действ" in lowered:
            return "active"
        # корень «ликвид» покрывает и «ликвидации», и «ликвидирована»
        if "процесс" in lowered and "ликвид" in lowered:
            return "liquidating"
        if "реорганиз" in lowered:
            return "reorganizing"
        if "ликвид" in lowered or "прекра" in lowered:
            return "liquidated"
        return None

    def _region_code(self, block) -> str | None:
        if isinstance(block, dict):
            code = block.get("Код")
            return str(code).zfill(2) if code else None
        if block is not None and str(block).isdigit():
            return str(block).zfill(2)
        return None

    def _region_name(self, block) -> str | None:
        if isinstance(block, dict):
            return block.get("Наим")
        return None if block is None or str(block).isdigit() else str(block)

    def _okved_code(self, block) -> str | None:
        if isinstance(block, dict):
            return block.get("Код")
        return block if isinstance(block, str) else None

    def _okved_list(self, response: dict) -> list[str]:
        codes = []
        main = normalize_okved(self._okved_code(response.get("ОКВЭД")))
        if main:
            codes.append(main)
        for extra in response.get("ОКВЭДДоп") or []:
            code = normalize_okved(self._okved_code(extra))
            if code and code not in codes:
                codes.append(code)
        return codes

    def _manager(self, managers) -> str | None:
        if not managers:
            return None
        first = managers[0] if isinstance(managers, list) else managers
        return first.get("ФИО") if isinstance(first, dict) else None

    def _apply_tax(self, dto: CompanyDTO, taxes: dict) -> None:
        if not taxes or "ОсобРежим" not in taxes:
            # блока нет — это ответ поиска, а не карточки: данных о режиме просто не было
            dto.tax_status = TaxStatus.UNKNOWN
            return

        raw_regimes = taxes.get("ОсобРежим") or []
        found = [r for r in raw_regimes if any(s in str(r).upper() for s in _SPECIAL_REGIMES)]
        dto.tax_status = resolve_tax_status(registry_checked=True, found_regimes=found)
        dto.tax_regimes = [str(r) for r in found]
        dto.tax_source = self.name
        dto.fields_available.add("tax_status")

    def _mark_available(self, dto: CompanyDTO, response: dict) -> None:
        if response.get("Статус") is not None:
            dto.fields_available.add("status")
        if response.get("Регион") is not None:
            dto.fields_available.add("region_code")
        if response.get("ДатаРег"):
            dto.fields_available.add("registration_date")
        if response.get("ОКВЭД"):
            dto.fields_available.add("main_okved")
        if response.get("Контакты") is not None:
            dto.fields_available.add("contacts")
