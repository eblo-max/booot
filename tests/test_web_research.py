"""Проверка веб-агента.

Главное здесь — не то, что агент что-то находит, а то, что он отбрасывает
контакты, которых не было ни на одной открытой странице. Выдуманная почта
выглядит достовернее мусорной и тише отравляет базу.
"""

import json

import pytest

from src.domain.company import CompanyDTO
from src.services.web_research import WebResearcher, collect_source_text


class FakeBlock:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class FakeResponse:
    def __init__(self, payload: dict, sources: dict, stop_reason: str = "end_turn"):
        self.content = [FakeBlock(json.dumps(payload, ensure_ascii=False))]
        self.stop_reason = stop_reason
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
    async def test_not_found_returns_empty(self):
        researcher = make(
            {
                "emails": [{"value": "a@b.ru", "source_url": "", "note": ""}],
                "phones": [],
                "website": "",
                "activity": "",
                "signals": [],
                "not_found": True,
            },
            page_text="a@b.ru",
        )
        result = await researcher.research(company())
        assert result.emails == [] and result.ok

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
        tool_types = {t["type"] for t in kwargs["tools"]}
        assert "web_search_20260209" in tool_types
        assert "web_fetch_20260209" in tool_types
        # на Sonnet 5 параметры сэмплирования отвергаются
        assert "temperature" not in kwargs and "top_p" not in kwargs
        assert kwargs["thinking"] == {"type": "adaptive"}

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
