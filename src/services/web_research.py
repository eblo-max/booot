"""Поиск информации о компании в вебе через Claude с серверными инструментами.

Ключевое проектное решение: модель НЕ является источником контактов.
Она умеет сочинить правдоподобную почту, которой на странице нет, — такая
запись выглядит достоверно и молча отравляет базу.

Поэтому:
  1. собираем весь текст, реально пришедший от web_search и web_fetch;
  2. вытаскиваем из него почты и телефоны регулярным выражением;
  3. всё, что модель вернула, но чего нет в этом тексте, отбрасываем;
  4. совпадение домена почты с доменом сайта повышает уровень доверия.

Модель работает фильтром и классификатором, а не источником фактов.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any

import structlog

from src.domain.company import CompanyDTO
from src.domain.normalize import normalize_emails, normalize_phones, normalize_website

log = structlog.get_logger()

MODEL = "claude-sonnet-5"
MAX_TOOL_ROUNDS = 4

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?:\+7|8)[\s\-(]*\d{3}[\s\-)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}")

SYSTEM = """Ты помогаешь проверять российские компании по открытым источникам.

Жёсткие правила:
1. Переписывай контакты ТОЛЬКО дословно со страниц, которые ты открыл.
2. Никогда не достраивай и не угадывай адрес по названию компании или домену.
   Если почты на странице нет — оставь список пустым.
3. Для каждого контакта укажи URL страницы, где он написан.
4. Если нашёл контакты другой организации — не включай их.

Отсутствие данных — нормальный результат. Выдуманные данные — брак."""

SCHEMA = {
    "type": "object",
    "properties": {
        "emails": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "value": {"type": "string"},
                    "source_url": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["value", "source_url", "note"],
                "additionalProperties": False,
            },
        },
        "phones": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "value": {"type": "string"},
                    "source_url": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["value", "source_url", "note"],
                "additionalProperties": False,
            },
        },
        "website": {"type": "string"},
        "activity": {"type": "string"},
        "signals": {"type": "array", "items": {"type": "string"}},
        "not_found": {"type": "boolean"},
    },
    "required": ["emails", "phones", "website", "activity", "signals", "not_found"],
    "additionalProperties": False,
}


@dataclass
class VerifiedContact:
    value: str
    source_url: str
    note: str = ""
    domain_match: bool = False


@dataclass
class ResearchResult:
    emails: list[VerifiedContact] = field(default_factory=list)
    phones: list[VerifiedContact] = field(default_factory=list)
    website: str | None = None
    activity: str = ""
    signals: list[str] = field(default_factory=list)
    discarded: list[str] = field(default_factory=list)  # что отбраковали как выдуманное
    pages_read: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def collect_source_text(payload: Any) -> str:
    """Весь текст, пришедший от инструментов поиска и загрузки.

    Обходим ответ рекурсивно вместо разбора типов блоков: формы блоков
    у разных инструментов различаются, а нам нужен любой текст, который
    реально был получен из сети.
    """
    chunks: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, str):
            chunks.append(node)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return "\n".join(chunks)


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


class WebResearcher:
    def __init__(self, client, model: str = MODEL):
        self.client = client
        self.model = model

    def _prompt(self, company: CompanyDTO) -> str:
        lines = [f"Компания: {company.name}"]
        if company.full_name:
            lines.append(f"Полное наименование: {company.full_name}")
        if company.inn:
            lines.append(f"ИНН: {company.inn}")
        if company.ogrn:
            lines.append(f"ОГРН: {company.ogrn}")
        if company.region_name:
            lines.append(f"Регион: {company.region_name}")
        if company.main_okved:
            lines.append(f"Основной ОКВЭД: {company.main_okved}")
        if company.website:
            lines.append(f"Известный сайт: {company.website}")

        lines += [
            "",
            "Найди официальный сайт этой компании и открой страницу контактов.",
            "Выпиши почты и телефоны дословно, с URL страницы для каждого.",
            "Коротко опиши, чем компания занимается, по её сайту.",
            "Отметь заметные признаки активности: новости, вакансии, проекты.",
            "Если это точно другая организация — верни not_found.",
        ]
        return "\n".join(lines)

    async def research(self, company: CompanyDTO) -> ResearchResult:
        messages = [{"role": "user", "content": self._prompt(company)}]
        collected_text: list[str] = []
        response = None

        try:
            for _ in range(MAX_TOOL_ROUNDS):
                response = await self.client.messages.create(
                    model=self.model,
                    max_tokens=8000,
                    system=SYSTEM,
                    thinking={"type": "adaptive"},
                    output_config={
                        "effort": "medium",
                        "format": {"type": "json_schema", "schema": SCHEMA},
                    },
                    tools=[
                        {"type": "web_search_20260209", "name": "web_search", "max_uses": 6},
                        {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 6},
                    ],
                    messages=messages,
                )
                collected_text.append(collect_source_text(self._dump(response)))

                if response.stop_reason != "pause_turn":
                    break
                # серверный инструмент упёрся в лимит итераций — продолжаем ту же реплику
                messages = messages[:1] + [{"role": "assistant", "content": response.content}]

            if response is not None and response.stop_reason == "refusal":
                return ResearchResult(error="Запрос отклонён политиками безопасности модели")
        except Exception as exc:  # noqa: BLE001 — падение поиска не должно ронять бота
            log.warning("web_research_failed", inn=company.inn, error=str(exc))
            return ResearchResult(error=f"{type(exc).__name__}: {exc}")

        raw = self._extract_json(response)
        if raw is None:
            return ResearchResult(error="Модель не вернула структурированный ответ")

        return self._verify(raw, "\n".join(collected_text), company)

    def _dump(self, response) -> Any:
        for attr in ("model_dump", "to_dict"):
            method = getattr(response, attr, None)
            if callable(method):
                return method()
        return response

    def _extract_json(self, response) -> dict | None:
        for block in getattr(response, "content", []) or []:
            if getattr(block, "type", None) != "text":
                continue
            try:
                return json.loads(block.text)
            except (ValueError, AttributeError):
                continue
        return None

    # --- проверка -----------------------------------------------------------

    def _verify(self, raw: dict, corpus: str, company: CompanyDTO) -> ResearchResult:
        if raw.get("not_found"):
            return ResearchResult(activity="", signals=[])

        site = normalize_website(raw.get("website")) or company.website
        site_domain = site.split("/")[0] if site else None

        real_emails = {e.lower() for e in _EMAIL_RE.findall(corpus)}
        # телефоны с найденных страниц приводим к тому же виду, что и ответ модели:
        # иначе «8 (495) …» на странице не совпадёт с «+7495…» после нормализации
        real_phones = {
            _digits(p) for p in normalize_phones(_PHONE_RE.findall(corpus))
        }

        result = ResearchResult(
            website=site,
            activity=(raw.get("activity") or "").strip()[:600],
            signals=[s.strip() for s in (raw.get("signals") or []) if s.strip()][:5],
            pages_read=corpus.count("http"),
        )

        for item in raw.get("emails") or []:
            value = (item.get("value") or "").strip().lower()
            if not normalize_emails([value]):
                continue
            if value not in real_emails:
                # модель вернула адрес, которого нет ни на одной открытой странице
                result.discarded.append(value)
                log.warning("web_research_hallucinated_email", inn=company.inn, value=value)
                continue
            domain = value.split("@")[-1]
            result.emails.append(
                VerifiedContact(
                    value=value,
                    source_url=(item.get("source_url") or "")[:300],
                    note=(item.get("note") or "")[:120],
                    domain_match=bool(site_domain and site_domain.endswith(domain)),
                )
            )

        for item in raw.get("phones") or []:
            normalized = normalize_phones([item.get("value") or ""])
            if not normalized:
                continue
            if _digits(normalized[0]) not in real_phones:
                result.discarded.append(item.get("value") or "")
                continue
            result.phones.append(
                VerifiedContact(
                    value=normalized[0],
                    source_url=(item.get("source_url") or "")[:300],
                    note=(item.get("note") or "")[:120],
                )
            )

        # почты с домена компании достовернее найденных на стороне
        result.emails.sort(key=lambda c: not c.domain_match)
        return result
