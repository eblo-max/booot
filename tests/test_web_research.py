"""Проверка веб-агента.

Главное здесь — не то, что агент что-то находит, а то, что он отбрасывает
контакты, которых не было ни на одной открытой странице. Выдуманная почта
выглядит достовернее мусорной и тише отравляет базу.
"""


import pytest

from src.domain.company import CompanyDTO
from src.services.web_research import WebResearcher, collect_source_text


class SubmitBlock:
    """Итог модель отдаёт вызовом инструмента, а не текстом."""

    def __init__(self, payload: dict):
        self.type = "tool_use"
        self.name = "submit_findings"
        self.input = payload


class FakeUsage:
    def __init__(self):
        self.input_tokens = 1000
        self.server_tool_use = type(
            "T", (), {"web_search_requests": 2, "web_fetch_requests": 1}
        )()


class FakeResponse:
    def __init__(self, payload: dict, sources: dict, stop_reason: str = "tool_use"):
        self.content = [SubmitBlock(payload)] if payload else []
        self.stop_reason = stop_reason
        self.usage = FakeUsage()
        self._sources = sources

    def model_dump(self):
        return self._sources


class FakeMessages:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        return self.response


class FakeClient:
    def __init__(self, response):
        self.messages = FakeMessages(response)


def company() -> CompanyDTO:
    return CompanyDTO(
        inn="7707083893",
        ogrn="1027700132195",
        name='ООО "СТРОЙМОНТАЖ"',
        website="stroymontazh.ru",
    )


def make(payload: dict, page_text: str) -> WebResearcher:
    sources = {"content": [{"type": "web_fetch_tool_result", "text": page_text}]}
    return WebResearcher(FakeClient(FakeResponse(payload, sources)))


class TestHallucinationGuard:
    async def test_email_absent_from_pages_is_discarded(self):
        """Модель вернула правдоподобный адрес, которого на странице нет."""
        researcher = make(
            {
                "emails": [{"value": "info@stroymontazh.ru", "source_url": "http://x", "note": ""}],
                "phones": [],
                "website": "stroymontazh.ru",
                "activity": "",
                "signals": [],
                "not_found": False,
            },
            page_text="Наш телефон +7 (495) 111-22-33. Почту не указываем.",
        )
        result = await researcher.research(company())
        assert result.emails == []
        assert "info@stroymontazh.ru" in result.discarded

    async def test_email_present_on_page_survives(self):
        researcher = make(
            {
                "emails": [{"value": "zakaz@stroymontazh.ru", "source_url": "http://x", "note": "отдел"}],
                "phones": [],
                "website": "stroymontazh.ru",
                "activity": "",
                "signals": [],
                "not_found": False,
            },
            page_text="Пишите на zakaz@stroymontazh.ru",
        )
        result = await researcher.research(company())
        assert [e.value for e in result.emails] == ["zakaz@stroymontazh.ru"]
        assert result.discarded == []

    async def test_phone_written_differently_still_matches(self):
        """На странице «8 (495)…», модель вернула «+7 495…» — это один номер."""
        researcher = make(
            {
                "emails": [],
                "phones": [{"value": "+7 495 111-22-33", "source_url": "http://x", "note": ""}],
                "website": "",
                "activity": "",
                "signals": [],
                "not_found": False,
            },
            page_text="Телефон: 8 (495) 111-22-33",
        )
        result = await researcher.research(company())
        assert [p.value for p in result.phones] == ["+74951112233"]

    async def test_invented_phone_is_discarded(self):
        researcher = make(
            {
                "emails": [],
                "phones": [{"value": "+7 495 999-88-77", "source_url": "http://x", "note": ""}],
                "website": "",
                "activity": "",
                "signals": [],
                "not_found": False,
            },
            page_text="Телефон: 8 (495) 111-22-33",
        )
        result = await researcher.research(company())
        assert result.phones == []


class TestRanking:
    async def test_own_domain_email_ranks_first(self):
        researcher = make(
            {
                "emails": [
                    {"value": "manager@mail.ru", "source_url": "http://x", "note": ""},
                    {"value": "zakaz@stroymontazh.ru", "source_url": "http://x", "note": ""},
                ],
                "phones": [],
                "website": "stroymontazh.ru",
                "activity": "",
                "signals": [],
                "not_found": False,
            },
            page_text="manager@mail.ru zakaz@stroymontazh.ru",
        )
        result = await researcher.research(company())
        assert result.emails[0].value == "zakaz@stroymontazh.ru"
        assert result.emails[0].domain_match is True
        assert result.emails[1].domain_match is False


class TestOutcomes:
    async def test_not_found_marks_uncertain_but_keeps_findings(self):
        """Регрессия: раньше not_found стирал контакты, которые реально были
        на странице, и пользователь видел «почт не нашлось» при найденных почтах."""
        researcher = make(
            {
                "emails": [{"value": "a@b.ru", "source_url": "http://x", "note": ""}],
                "phones": [],
                "website": "",
                "activity": "",
                "signals": [],
                "not_found": True,
            },
            page_text="пишите на a@b.ru",
        )
        result = await researcher.research(company())
        assert [e.value for e in result.emails] == ["a@b.ru"]
        assert result.uncertain is True
        assert result.ok

    async def test_activity_and_signals_captured(self):
        researcher = make(
            {
                "emails": [],
                "phones": [],
                "website": "stroymontazh.ru",
                "activity": "Монолитное строительство и отделка",
                "signals": ["Открыта вакансия прораба", "Сдан объект в Химках"],
                "not_found": False,
            },
            page_text="",
        )
        result = await researcher.research(company())
        assert "Монолитное" in result.activity
        assert len(result.signals) == 2

    async def test_api_failure_is_reported_not_raised(self):
        class Boom:
            async def create(self, **kwargs):
                raise RuntimeError("connection reset")

        class BoomClient:
            messages = Boom()

        result = await WebResearcher(BoomClient()).research(company())
        assert not result.ok and "connection reset" in result.error

    async def test_refusal_is_reported(self):
        sources = {"content": []}
        response = FakeResponse({}, sources, stop_reason="refusal")
        result = await WebResearcher(FakeClient(response)).research(company())
        assert not result.ok and "политиками" in result.error


class TestRequestShape:
    async def test_uses_sonnet_and_web_tools(self):
        researcher = make(
            {
                "emails": [], "phones": [], "website": "",
                "activity": "", "signals": [], "not_found": False,
            },
            page_text="",
        )
        await researcher.research(company())
        kwargs = researcher.client.messages.last_kwargs

        assert kwargs["model"] == "claude-sonnet-5"
        # у пользовательского инструмента ключа type нет — только name
        by_type = {t["type"]: t for t in kwargs["tools"] if "type" in t}
        assert "web_search_20260209" in by_type
        assert "web_fetch_20260209" in by_type
        # поиск дороже загрузки страниц — лимитов должно быть больше на fetch
        assert by_type["web_search_20260209"]["max_uses"] < by_type["web_fetch_20260209"]["max_uses"]
        assert by_type["web_fetch_20260209"]["max_content_tokens"] <= 20000
        # на medium модель не открывала страницы вовсе
        assert kwargs["output_config"]["effort"] == "high"
        # на Sonnet 5 параметры сэмплирования отвергаются API
        assert "temperature" not in kwargs and "top_p" not in kwargs
        assert kwargs["thinking"] == {"type": "adaptive"}

    async def test_result_is_collected_by_tool_not_forced_format(self):
        """Регрессия: output_config.format обрывал агентный цикл — модель спешила
        выдать JSON по схеме и ни разу не вызывала web_fetch."""
        researcher = make(
            {
                "emails": [], "phones": [], "website": "",
                "activity": "", "signals": [], "not_found": False,
            },
            page_text="",
        )
        await researcher.research(company())
        kwargs = researcher.client.messages.last_kwargs

        assert "format" not in kwargs.get("output_config", {})
        assert any(t.get("name") == "submit_findings" for t in kwargs["tools"])

    async def test_usage_is_recorded(self):
        researcher = make(
            {
                "emails": [], "phones": [], "website": "",
                "activity": "", "signals": [], "not_found": False,
            },
            page_text="",
        )
        result = await researcher.research(company())
        assert result.searches == 2 and result.fetches == 1
        assert result.input_tokens == 1000

    async def test_prompt_contains_identifiers(self):
        researcher = make(
            {
                "emails": [], "phones": [], "website": "",
                "activity": "", "signals": [], "not_found": False,
            },
            page_text="",
        )
        await researcher.research(company())
        prompt = researcher.client.messages.last_kwargs["messages"][0]["content"]
        assert "7707083893" in prompt and "СТРОЙМОНТАЖ" in prompt


class TestSourceCollection:
    def test_walks_nested_structures(self):
        payload = {"a": ["x", {"b": "y"}], "c": {"d": ["z"]}}
        text = collect_source_text(payload)
        assert all(part in text for part in ("x", "y", "z"))

    def test_handles_empty(self):
        assert collect_source_text({}) == ""


@pytest.mark.parametrize("bad", ["не почта", "", "@@", "a@b"])
def test_email_regex_rejects_garbage(bad):
    from src.services.web_research import _EMAIL_RE

    assert not _EMAIL_RE.fullmatch(bad)
