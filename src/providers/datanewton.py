"""DataNewton.

Формат сверен с живым API (тестовый ключ, июль 2026). Проверено:
  GET /v1/counterparty?key=…&inn=…&filters=OKVED_BLOCK,CONTACT_BLOCK,…
  POST /v1/batchCardsByFilters?key=…&limit=…&offset=…  (фильтры в теле JSON)

Метод фильтров требует отдельного платного тарифа: с ключом без доступа
приходит {"code": 11, "message": "API ключ не подходит"}.
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
    ProviderAuthError,
    ProviderQuotaExceeded,
    ProviderRateLimited,
    ProviderUnavailable,
)

log = structlog.get_logger()

BASE_URL = "https://api.datanewton.ru/v1"
SECTIONS = "OKVED_BLOCK,CONTACT_BLOCK,ADDRESS_BLOCK,NEGATIVE_LISTS_BLOCK"

# ключи tax_mode_info -> человекочитаемое название режима
_REGIMES = {
    "usn_sign": "УСН",
    "ausn_sign": "АУСН",
    "eshn_sign": "ЕСХН",
    "psn_sign": "ПСН",
    "npd_sign": "НПД",
    "envd_sign": "ЕНВД",
    "srp_sign": "СРП",
}

_STATUS = {"active": "active", "liquidating": "liquidating", "liquidated": "liquidated"}

# Контакты приходят из разных источников и сильно различаются по достоверности.
# На живом ответе видно: у ПАО Сбербанк 50 почт, из них с меткой ЕГРЮЛ одна,
# остальные подтянуты парсером с сайтов и относятся к посторонним компаниям.
_SOURCE_RANK = {"ЕГРЮЛ": 0, "Публичные источники": 1, "С сайта компании": 2}
_MAX_CONTACTS = 5


def _rank_contacts(items: list[dict] | None) -> list[str]:
    """Сортирует по достоверности источника и обрезает хвост."""
    if not items:
        return []
    ordered = sorted(
        (i for i in items if isinstance(i, dict) and i.get("value")),
        key=lambda i: _SOURCE_RANK.get(i.get("source_label") or "", 9),
    )
    return [i["value"] for i in ordered[:_MAX_CONTACTS]]


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
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


class DataNewtonProvider(CompanyProvider):
    name = "DataNewton"
    capabilities = ProviderCapabilities(
        mass_search=True,
        supported_filters={"regions", "okved"},
        has_financials=True,
        has_contacts=True,
        has_tax_regime=True,
        rate_limit_rps=3.0,  # документировано 200/мин, берём с запасом
    )

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._client = httpx.AsyncClient(timeout=60, follow_redirects=True)

    async def close(self) -> None:
        await self._client.aclose()

    # --- транспорт ---------------------------------------------------------

    def _raise_for_payload(self, payload: dict, status_code: int) -> None:
        code = payload.get("code")
        message = payload.get("message", "")
        if code in (11, 12) or status_code in (401, 403):
            raise ProviderAuthError(f"{message} (code={code})")
        if status_code == 429:
            raise ProviderRateLimited(message or "429 Too Many Requests")
        if code == 3:
            raise ProviderQuotaExceeded(message or "лимит тарифа исчерпан")

    async def _get(self, path: str, params: dict) -> dict:
        try:
            response = await self._client.get(f"{BASE_URL}{path}", params={**params, "key": self.api_key})
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(str(exc)) from exc

        payload = self._json(response)
        self._raise_for_payload(payload, response.status_code)
        return payload

    def _json(self, response: httpx.Response) -> dict:
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderUnavailable(
                f"Неожиданный ответ (HTTP {response.status_code}): {response.text[:200]}"
            ) from exc

    # --- интерфейс ---------------------------------------------------------

    async def check_connection(self) -> ConnectionStatus:
        try:
            payload = await self._get("/counterparty", {"inn": "7707083893"})
        except ProviderAuthError as exc:
            return ConnectionStatus(ok=False, message=f"ключ отклонён: {exc}")
        except Exception as exc:  # noqa: BLE001
            return ConnectionStatus(ok=False, message=str(exc))

        left = payload.get("available_count")
        return ConnectionStatus(ok=True, message="соединение установлено", quota_left=left)

    async def get_company(self, inn_or_ogrn: str) -> CompanyDTO | None:
        key = "ogrn" if len(inn_or_ogrn.strip()) in (13, 15) else "inn"
        payload = await self._get("/counterparty", {key: inn_or_ogrn.strip(), "filters": SECTIONS})
        if payload.get("code") == 2:
            # «Контрагент неоднозначно определён» — по ИНН нашлось несколько записей
            return None
        if not payload.get("company"):
            return None
        return self.normalize_response(payload)

    async def search(
        self, criteria: SearchCriteria, limit: int, cursor: str | None = None
    ) -> SearchPage:
        offset = int(cursor or 0)
        body: dict = {}
        if criteria.regions:
            body["region_codes"] = list(criteria.regions)
        if criteria.okved_main:
            body["okveds"] = list(criteria.okved_main)

        try:
            response = await self._client.post(
                f"{BASE_URL}/batchCardsByFilters",
                params={"key": self.api_key, "limit": limit, "offset": offset},
                json=body,
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(str(exc)) from exc

        payload = self._json(response)
        if payload.get("code") == 11:
            raise ProviderAuthError(
                "Метод массового поиска (Фильтры API) не входит в ваш тариф DataNewton. "
                "Используйте Checko для списка или подключите тариф «Фильтры API»."
            )
        self._raise_for_payload(payload, response.status_code)

        # ВНИМАНИЕ: форма ответа этого метода на живом ключе не проверялась —
        # тестовый ключ доступа к нему не даёт. Разбор намеренно терпимый.
        raw_items = payload.get("data") or payload.get("companies") or payload.get("items") or []
        items = [self.normalize_response(item) for item in raw_items if isinstance(item, dict)]
        next_cursor = str(offset + limit) if len(items) >= limit else None
        return SearchPage(items=items, next_cursor=next_cursor, total_hint=payload.get("count"))

    # --- разбор ------------------------------------------------------------

    def normalize_response(self, response: dict) -> CompanyDTO:
        company = response.get("company") or {}
        names = company.get("company_names") or {}
        address = company.get("address") or {}
        status_block = company.get("status") or {}
        contacts = company.get("contacts") or {}

        okveds = company.get("okveds") or []
        main_code = next((o.get("code") for o in okveds if o.get("main")), None)

        dto = CompanyDTO(
            inn=normalize_inn(response.get("inn")),
            ogrn=normalize_ogrn(response.get("ogrn")),
            name=names.get("short_name") or names.get("full_name") or "",
            full_name=names.get("full_name"),
            opf=self._opf(names.get("short_name") or "", company.get("opf")),
            status=_STATUS.get(status_block.get("status_eng_short") or "", None),
            region_code=address.get("region_code"),
            region_name=address.get("region"),
            registration_date=_date(company.get("registration_date")),
            main_okved=main_code,
            okved_list=[o["code"] for o in okveds if o.get("code")],
            manager_name=self._manager(company.get("managers")),
            phones=normalize_phones(_rank_contacts(contacts.get("phones"))),
            emails=normalize_emails(_rank_contacts(contacts.get("emails"))),
            website=self._website(contacts),
            source=self.name,
            raw=response,
        )
        self._apply_tax(dto, company.get("tax_mode_info") or {})
        self._apply_available(dto, company)
        return dto

    def _opf(self, short_name: str, opf_text: str | None) -> str | None:
        text = f"{short_name} {opf_text or ''}".upper()
        for code in ("ПАО", "НАО", "ООО", "АО", "ИП"):
            if code in text:
                return code
        return None

    def _manager(self, managers) -> str | None:
        if not managers:
            return None
        first = managers[0]
        return first.get("fio") or first.get("full_name") or first.get("name")

    def _website(self, contacts: dict) -> str | None:
        for value in _rank_contacts(contacts.get("websites") or contacts.get("sites")):
            normalized = normalize_website(value)
            if normalized:
                return normalized
        return None

    def _apply_tax(self, dto: CompanyDTO, tax_info: dict) -> None:
        if not tax_info:
            dto.tax_status = TaxStatus.UNKNOWN
            return

        found = [label for key, label in _REGIMES.items() if tax_info.get(key)]
        dto.tax_status = resolve_tax_status(
            registry_checked=True,
            found_regimes=found,
            # источник прямо утверждает применение общей системы
            source_states_osno=bool(tax_info.get("common_mode")) and not found,
        )
        dto.tax_regimes = found
        dto.tax_source = self.name
        dto.source_updated_at = self._publication(tax_info)
        dto.fields_available.add("tax_status")

    def _publication(self, tax_info: dict) -> datetime | None:
        published = _date(tax_info.get("publication_date"))
        return datetime.combine(published, datetime.min.time()) if published else None

    def _apply_available(self, dto: CompanyDTO, company: dict) -> None:
        for key, field in (
            ("registration_date", "registration_date"),
            ("workers_count", "employees"),
        ):
            if company.get(key) is not None:
                dto.fields_available.add(field)
        if company.get("address"):
            dto.fields_available.add("region_code")
        if company.get("okveds"):
            dto.fields_available.add("main_okved")
        if company.get("status"):
            dto.fields_available.add("status")
        if company.get("contacts"):
            dto.fields_available.add("contacts")
