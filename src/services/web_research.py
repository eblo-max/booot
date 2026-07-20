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
# каждый повтор — это полный агентный цикл на минуты; четыре подряд давали
# многоминутное молчание в чате, двух достаточно
MAX_TOOL_ROUNDS = 2
# жёсткий потолок на весь разбор: по умолчанию клиент ждёт 10 минут,
# и пользователь всё это время смотрит на «ищу…»
TIMEOUT_SECONDS = 150.0

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?:\+7|8)[\s\-(]*\d{3}[\s\-)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}")

SYSTEM = """Ты помогаешь проверять российские компании по открытым источникам.

Порядок работы:
1. Найди поиском официальный сайт компании.
2. ОБЯЗАТЕЛЬНО открой инструментом web_fetch главную страницу сайта и
   страницу контактов. Одного поиска недостаточно: контакты почти всегда
   лежат на странице, а не в поисковой выдаче.
3. Выпиши почты и телефоны дословно оттуда.

Жёсткие правила:
- Переписывай контакты ТОЛЬКО дословно с открытых страниц или из текста
  поисковой выдачи. Никогда не достраивай адрес по названию или домену.
- Для каждого контакта укажи URL, где он написан.
- Не включай контакты других организаций.

Про not_found: ставь true ТОЛЬКО если найденная организация — заведомо
другая компания (не совпадает название или ИНН). Неуверенность, скудость
данных или отсутствие контактов — это НЕ not_found: верни то, что нашёл,
даже если это только описание деятельности.

Отсутствие данных — нормальный результат. Выдуманные данные — брак."""

SUBMIT_TOOL_NAME = "submit_findings"

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

# Результат собирается инструментом, а не output_config.format: принудительный
# структурированный вывод обрывал агентный цикл — модель спешила выдать JSON по
# схеме и ни разу не доходила до web_fetch (проверено на живом API).
SUBMIT_TOOL = {
    "name": SUBMIT_TOOL_NAME,
    "description": (
        "Вызови ОДИН раз в самом конце, когда уже открыл сайт компании и страницу "
        "контактов. Передай найденное. Не вызывай, пока не поработал инструментами."
    ),
    "strict": True,
    "input_schema": SCHEMA,
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
    uncertain: bool = False  # модель не уверена, что нашла именно эту компанию
    error: str | None = None
    # что реально сделала модель — видно в логах и помогает диагностировать пустой ответ
    searches: int = 0
    fetches: int = 0
    input_tokens: int = 0

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
        # видно в логах сразу, а не только после ответа модели
        log.info("web_research_started", inn=company.inn, name=company.name)
        messages = [{"role": "user", "content": self._prompt(company)}]
        collected_text: list[str] = []
        response = None

        raw: dict | None = None
        try:
            for attempt in range(MAX_TOOL_ROUNDS):
                response = await self.client.messages.create(
                    model=self.model,
                    max_tokens=8000,
                    system=SYSTEM,
                    thinking={"type": "adaptive"},
                    # high давал слишком долгий разбор; цикл теперь ведёт
                    # submit_findings, а не принудительный формат, и medium хватает
                    output_config={"effort": "medium"},
                    timeout=TIMEOUT_SECONDS,
                    tools=[
                        # поиск дорогой: две выдачи дают ~200 тыс. входных токенов,
                        # поэтому ограничиваем его и переносим работу на загрузку страниц
                        {"type": "web_search_20260209", "name": "web_search", "max_uses": 3},
                        {
                            "type": "web_fetch_20260209",
                            "name": "web_fetch",
                            "max_uses": 5,
                            "max_content_tokens": 20000,
                        },
                        SUBMIT_TOOL,
                    ],
                    messages=messages,
                )
                collected_text.append(collect_source_text(self._dump(response)))

                if response.stop_reason == "refusal":
                    return ResearchResult(error="Запрос отклонён политиками безопасности модели")

                raw = self._find_submission(response)
                if raw is not None:
                    break

                messages = messages[:1] + [{"role": "assistant", "content": response.content}]
                if response.stop_reason == "pause_turn":
                    # серверный инструмент упёрся в лимит итераций — продолжаем реплику
                    continue
                if attempt < MAX_TOOL_ROUNDS - 1:
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                f"Заверши работу: вызови {SUBMIT_TOOL_NAME} с тем, "
                                "что удалось найти."
                            ),
                        }
                    )
        except Exception as exc:  # noqa: BLE001 — падение поиска не должно ронять бота
            log.warning("web_research_failed", inn=company.inn, error=str(exc))
            if "timeout" in type(exc).__name__.lower():
                return ResearchResult(
                    error="Разбор занял больше двух с половиной минут и был прерван. "
                    "Попробуйте ещё раз — обычно со второго раза быстрее."
                )
            return ResearchResult(error=f"{type(exc).__name__}: {exc}")

        if raw is None:
            return ResearchResult(error="Модель не передала результат")

        result = self._verify(raw, "\n".join(collected_text), company)
        self._attach_usage(result, response)
        log.info(
            "web_research_done",
            inn=company.inn,
            emails=len(result.emails),
            discarded=len(result.discarded),
            searches=result.searches,
            fetches=result.fetches,
            uncertain=result.uncertain,
            input_tokens=result.input_tokens,
        )
        return result

    def _attach_usage(self, result: ResearchResult, response) -> None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        result.input_tokens = getattr(usage, "input_tokens", 0) or 0
        tools = getattr(usage, "server_tool_use", None)
        if tools is not None:
            result.searches = getattr(tools, "web_search_requests", 0) or 0
            result.fetches = getattr(tools, "web_fetch_requests", 0) or 0

    def _dump(self, response) -> Any:
        for attr in ("model_dump", "to_dict"):
            method = getattr(response, attr, None)
            if callable(method):
                return method()
        return response

    def _find_submission(self, response) -> dict | None:
        """Находит вызов submit_findings — им модель отдаёт итог."""
        for block in getattr(response, "content", []) or []:
            if getattr(block, "type", None) != "tool_use":
                continue
            if getattr(block, "name", None) != SUBMIT_TOOL_NAME:
                continue
            payload = getattr(block, "input", None)
            if isinstance(payload, dict):
                return payload
            if isinstance(payload, str):
                try:
                    return json.loads(payload)
                except ValueError:
                    return None
        return None

    # --- проверка -----------------------------------------------------------

    def _verify(self, raw: dict, corpus: str, company: CompanyDTO) -> ResearchResult:
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
            # модель считает, что нашла другую организацию; результат показываем,
            # но помечаем — раньше этот флаг молча стирал проверенные контакты
            uncertain=bool(raw.get("not_found")),
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
