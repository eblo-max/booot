"""Описание шагов мастера /new_search.

Каждый шаг — данные, а не отдельный обработчик: набор кнопок с готовыми значениями
плюс возможность ввести ответ текстом. Парсер поднимает ValueError с подсказкой.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from src.domain.normalize import normalize_okved

SKIP = "__skip__"


@dataclass
class Step:
    key: str
    prompt: str
    options: list[tuple[str, object]] = field(default_factory=list)
    parse: Callable[[str], object] | None = None
    hint: str = ""
    columns: int = 2


def _parse_name(text: str) -> str:
    text = text.strip()
    if len(text) < 2:
        raise ValueError("Название слишком короткое — минимум 2 символа.")
    return text[:128]


def _parse_regions(text: str) -> list[str]:
    parts = [p.strip() for p in text.replace(".", ",").split(",") if p.strip()]
    codes = []
    for p in parts:
        if not p.isdigit() or not 1 <= len(p) <= 2:
            raise ValueError("Регион задаётся двузначным кодом ФНС: 77 — Москва, 50 — МО, 78 — СПб.")
        codes.append(p.zfill(2))
    return codes


def _parse_date(text: str) -> date | None:
    text = text.strip()
    for sep in (".", "-", "/"):
        parts = text.split(sep)
        if len(parts) == 3:
            try:
                d, m, y = (int(x) for x in parts)
                if y < 100:
                    y += 2000
                return date(y, m, d)
            except ValueError:
                break
    raise ValueError("Дата в формате ДД.ММ.ГГГГ, например 01.01.2020.")


def _parse_okved(text: str) -> list[str]:
    raw = [p.strip() for p in text.replace("\n", ",").replace(";", ",").split(",") if p.strip()]
    codes = []
    for item in raw:
        code = normalize_okved(item)
        if not code:
            raise ValueError(f"Не понял код «{item}». Формат: 41.20, 43.11 через запятую.")
        codes.append(code)
    if not codes:
        raise ValueError("Укажите хотя бы один код ОКВЭД или нажмите «Не важно».")
    return codes


def _parse_year(text: str) -> int:
    text = text.strip()
    if not text.isdigit() or not 2000 <= int(text) <= date.today().year:
        raise ValueError(f"Год числом, от 2000 до {date.today().year}.")
    return int(text)


def _parse_revenue(text: str) -> tuple[Decimal | None, Decimal | None]:
    cleaned = text.replace(" ", "").replace("_", "").replace("—", "-").replace("–", "-")
    parts = cleaned.split("-")
    if len(parts) != 2:
        raise ValueError("Диапазон через дефис: 5000000-500000000")
    try:
        low = Decimal(parts[0]) if parts[0] else None
        high = Decimal(parts[1]) if parts[1] else None
    except InvalidOperation as exc:
        raise ValueError("Только цифры и дефис: 5000000-500000000") from exc
    if low is not None and high is not None and low > high:
        raise ValueError("Нижняя граница больше верхней.")
    return low, high


def _parse_limit(text: str) -> int:
    text = text.strip()
    if not text.isdigit() or not 1 <= int(text) <= 500:
        raise ValueError("Число от 1 до 500.")
    return int(text)


STEPS: list[Step] = [
    Step(
        key="name",
        prompt="<b>Шаг 1/13.</b> Как назовём запрос?",
        hint="Например: Строительные ООО Москвы",
        parse=_parse_name,
    ),
    Step(
        key="regions",
        prompt="<b>Шаг 2/13.</b> Регион?",
        options=[
            ("Москва", ["77"]),
            ("Московская обл.", ["50"]),
            ("Москва + МО", ["77", "50"]),
            ("Санкт-Петербург", ["78"]),
            ("Вся РФ", []),
        ],
        parse=_parse_regions,
        hint="Или введите коды через запятую: 77, 50",
    ),
    Step(
        key="status",
        prompt="<b>Шаг 3/13.</b> Статус компаний?",
        options=[
            ("Только действующие", ["active"]),
            ("Действующие + ликвидируемые", ["active", "liquidating"]),
            ("Любой", []),
        ],
        columns=1,
    ),
    Step(
        key="opf",
        prompt="<b>Шаг 4/13.</b> Организационно-правовая форма?",
        options=[
            ("ООО", ["ООО"]),
            ("АО", ["АО"]),
            ("ООО + АО", ["ООО", "АО"]),
            ("ИП", ["ИП"]),
            ("Любая", []),
        ],
    ),
    Step(
        key="reg_date_from",
        prompt="<b>Шаг 5/13.</b> Дата регистрации — начало периода?",
        options=[("Не важно", None)],
        parse=_parse_date,
        hint="Формат ДД.ММ.ГГГГ, например 01.01.2020",
        columns=1,
    ),
    Step(
        key="reg_date_to",
        prompt="<b>Шаг 6/13.</b> Дата регистрации — конец периода?",
        options=[("Не важно", None), ("Сегодня", date.today())],
        parse=_parse_date,
        hint="Формат ДД.ММ.ГГГГ, например 31.12.2025",
    ),
    Step(
        key="okved_main",
        prompt="<b>Шаг 7/13.</b> Основные ОКВЭД?",
        options=[("Не важно", [])],
        parse=_parse_okved,
        hint="Через запятую: 41.20, 41.10, 42.11, 43.11, 43.12, 43.21\n"
        "Код группы покрывает вложенные: 41.2 включает 41.20",
        columns=1,
    ),
    Step(
        key="financial_year",
        prompt="<b>Шаг 8/13.</b> За какой год смотреть финансы?",
        options=[("2025", 2025), ("2024", 2024), ("2023", 2023), ("Не важно", None)],
        parse=_parse_year,
    ),
    Step(
        key="revenue",
        prompt="<b>Шаг 9/13.</b> Выручка, диапазон в рублях?",
        options=[
            ("5 млн — 500 млн", (Decimal("5000000"), Decimal("500000000"))),
            ("от 100 млн", (Decimal("100000000"), None)),
            ("Не важно", (None, None)),
        ],
        parse=_parse_revenue,
        hint="Или введите свой диапазон: 5000000-500000000",
        columns=1,
    ),
    Step(
        key="contacts_required",
        prompt="<b>Шаг 10/13.</b> Нужны контакты?",
        options=[
            ("Обязательно", "required"),
            ("Желательно", "preferred"),
            ("Не важно", "no"),
        ],
    ),
    Step(
        key="special_tax_regimes",
        prompt="<b>Шаг 11/13.</b> Специальные налоговые режимы (УСН, АУСН, ЕСХН)?",
        options=[
            ("Исключить", "exclude"),
            ("Допустимы", "allow"),
            ("Только спецрежимы", "only"),
        ],
        columns=1,
    ),
    Step(
        key="schedule",
        prompt="<b>Шаг 12/13.</b> Как часто проверять?",
        options=[("Ежедневно", "daily"), ("Раз в неделю", "weekly"), ("Только вручную", "manual")],
    ),
    Step(
        key="max_results_per_run",
        prompt="<b>Шаг 13/13.</b> Максимум результатов за один запуск?",
        options=[("10", 10), ("25", 25), ("50", 50), ("100", 100)],
        parse=_parse_limit,
    ),
]

STEP_BY_KEY = {s.key: s for s in STEPS}
