"""Описание шагов мастера /new_search.

Каждый шаг — данные, а не отдельный обработчик: набор кнопок с готовыми значениями
плюс возможность ввести ответ текстом. Парсер поднимает ValueError с подсказкой.

Шаги с multi=True накапливают список значений: кнопка переключает элемент,
"Готово" завершает шаг.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from src.domain.normalize import normalize_okved


@dataclass
class Step:
    key: str
    title: str  # короткая подпись для экрана редактирования
    prompt: str
    options: list[tuple[str, object]] = field(default_factory=list)
    parse: Callable[[str], object] | None = None
    hint: str = ""
    columns: int = 2
    multi: bool = False


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


def _parse_one_date(raw: str) -> date:
    raw = raw.strip()
    for sep in (".", "-", "/"):
        parts = raw.split(sep)
        if len(parts) == 3:
            try:
                d, m, y = (int(x) for x in parts)
                if y < 100:
                    y += 2000
                return date(y, m, d)
            except ValueError:
                break
    raise ValueError("Дата в формате ДД.ММ.ГГГГ, например 01.01.2020.")


def _parse_reg_period(text: str) -> dict:
    """Принимает диапазон «01.01.2020-31.12.2025» или «последние 30 дней»."""
    cleaned = text.strip().lower().replace("—", "-").replace("–", "-")

    digits = "".join(ch for ch in cleaned if ch.isdigit())
    if "дн" in cleaned and digits:
        days = int(digits)
        if not 1 <= days <= 3650:
            raise ValueError("Количество дней — от 1 до 3650.")
        return {"mode": "relative", "days": days}

    parts = [p.strip() for p in cleaned.split("-") if p.strip()]
    # дата сама содержит дефисы, поэтому разбор через дефис делаем только для точек
    if cleaned.count(".") >= 2:
        halves = cleaned.split("-")
        if len(halves) == 2:
            return {
                "mode": "absolute",
                "from": _parse_one_date(halves[0]),
                "to": _parse_one_date(halves[1]),
            }
        if len(parts) == 1:
            return {"mode": "absolute", "from": _parse_one_date(parts[0]), "to": None}
    raise ValueError(
        "Укажите диапазон «01.01.2020-31.12.2025» или относительный период «последние 30 дней»."
    )


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


def _parse_money(raw: str) -> Decimal:
    try:
        return Decimal(raw.replace(" ", "").replace("_", ""))
    except InvalidOperation as exc:
        raise ValueError("Только цифры, например 5000000") from exc


def _parse_revenue(text: str) -> tuple[Decimal | None, Decimal | None]:
    cleaned = text.replace(" ", "").replace("_", "").replace("—", "-").replace("–", "-")
    parts = cleaned.split("-")
    if len(parts) != 2:
        raise ValueError("Диапазон через дефис: 5000000-500000000")
    low = _parse_money(parts[0]) if parts[0] else None
    high = _parse_money(parts[1]) if parts[1] else None
    if low is not None and high is not None and low > high:
        raise ValueError("Нижняя граница больше верхней.")
    return low, high


def _parse_profit(text: str) -> Decimal | None:
    cleaned = text.strip().lower()
    if cleaned in ("не важно", "любая", "-"):
        return None
    return _parse_money(cleaned)


def _parse_limit(text: str) -> int:
    text = text.strip()
    if not text.isdigit() or not 1 <= int(text) <= 500:
        raise ValueError("Число от 1 до 500.")
    return int(text)


STEPS: list[Step] = [
    Step(
        key="name",
        title="Название",
        prompt="Как назовём запрос?",
        hint="Например: Строительные ООО Москвы",
        parse=_parse_name,
    ),
    Step(
        key="regions",
        title="Регион",
        prompt="Регион?",
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
        title="Статус",
        prompt="Статус компаний?",
        options=[
            ("Действующие", "active"),
            ("Ликвидируемые", "liquidating"),
            ("Ликвидированные", "liquidated"),
            ("Реорганизуемые", "reorganizing"),
        ],
        multi=True,
        hint="Отметьте нужные и нажмите «Готово». Ничего не выбрано — статус не важен.",
    ),
    Step(
        key="opf",
        title="ОПФ",
        prompt="Организационно-правовая форма?",
        options=[
            ("ООО", "ООО"),
            ("АО", "АО"),
            ("ПАО", "ПАО"),
            ("НАО", "НАО"),
            ("ИП", "ИП"),
        ],
        multi=True,
        hint="Отметьте нужные и нажмите «Готово». Ничего не выбрано — форма не важна.",
    ),
    Step(
        key="reg_period",
        title="Период регистрации",
        prompt="Дата регистрации?",
        options=[
            ("Последние 7 дней", {"mode": "relative", "days": 7}),
            ("Последние 30 дней", {"mode": "relative", "days": 30}),
            ("Последние 90 дней", {"mode": "relative", "days": 90}),
            ("Последний год", {"mode": "relative", "days": 365}),
            ("Не важно", None),
        ],
        parse=_parse_reg_period,
        hint=(
            "Относительный период пересчитывается при каждом запуске — "
            "для ежедневного мониторинга берите его.\n"
            "Свой диапазон: 01.01.2020-31.12.2025"
        ),
        columns=2,
    ),
    Step(
        key="okved_main",
        title="ОКВЭД",
        prompt="Коды ОКВЭД?",
        options=[("Не важно", [])],
        parse=_parse_okved,
        hint="Через запятую: 41.20, 41.10, 42.11, 43.11, 43.12, 43.21\n"
        "Код группы покрывает вложенные: 41.2 включает 41.20",
        columns=1,
    ),
    Step(
        key="okved_match_mode",
        title="Где искать ОКВЭД",
        prompt="Где проверять коды ОКВЭД?",
        options=[
            ("Только основной", "main_only"),
            ("Основной или дополнительные", "main_or_additional"),
        ],
        hint="По дополнительным находится заметно больше компаний, но выдача менее точная.",
        columns=1,
    ),
    Step(
        key="financial_year",
        title="Финансовый год",
        prompt="За какой год смотреть финансы?",
        options=[("2025", 2025), ("2024", 2024), ("2023", 2023), ("Не важно", None)],
        parse=_parse_year,
    ),
    Step(
        key="revenue",
        title="Выручка",
        prompt="Выручка, диапазон в рублях?",
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
        key="profit_min",
        title="Прибыль",
        prompt="Минимальная чистая прибыль?",
        options=[
            ("Не важно", None),
            ("от 1 млн", Decimal("1000000")),
            ("от 5 млн", Decimal("5000000")),
            ("Только прибыльные", Decimal("1")),
        ],
        parse=_parse_profit,
        hint="Или введите число: 2500000",
    ),
    Step(
        key="contacts",
        title="Контакты",
        prompt="Какие контакты обязательны?",
        options=[
            ("Телефон", "phone"),
            ("E-mail", "email"),
            ("Сайт", "website"),
            ("Любой контакт", "any"),
        ],
        multi=True,
        hint=(
            "Отметьте обязательные и нажмите «Готово».\n"
            "Ничего не выбрано — контакты желательны, но не обязательны."
        ),
    ),
    Step(
        key="special_tax_regimes",
        title="Спецрежимы",
        prompt="Специальные налоговые режимы (УСН, АУСН, ЕСХН)?",
        options=[
            ("Исключить", "exclude"),
            ("Допустимы", "allow"),
            ("Только спецрежимы", "only"),
        ],
        columns=1,
    ),
    Step(
        key="allow_unknown_tax_status",
        title="Неизвестный режим",
        prompt="Что делать с компаниями, у которых налоговый режим не удалось определить?",
        options=[("Показывать с пометкой", True), ("Отсеивать", False)],
        hint="Источник не всегда отдаёт данные о режиме. Отсев даёт точность ценой охвата.",
        columns=1,
    ),
    Step(
        key="schedule",
        title="Периодичность",
        prompt="Как часто проверять?",
        options=[("Ежедневно", "daily"), ("Раз в неделю", "weekly"), ("Только вручную", "manual")],
    ),
    Step(
        key="max_results_per_run",
        title="Лимит за запуск",
        prompt="Максимум результатов за один запуск?",
        options=[("10", 10), ("25", 25), ("50", 50), ("100", 100)],
        parse=_parse_limit,
    ),
]

STEP_INDEX = {s.key: i for i, s in enumerate(STEPS)}
