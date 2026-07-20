"""Описания наборов открытых данных ФНС.

Все наборы делят один конверт:
    Файл → Документ(ИдДок, ДатаДок, ДатаСост) → СведНП(НаимОрг, ИННЮЛ) + блок данных

Различается только блок данных, поэтому парсер общий, а здесь — карта полей.
Структуры сверены с XSD-схемами ФНС и реальными выгрузками.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DatasetSpec:
    code: str  # наш внутренний код
    slug: str  # часть URL на file.nalog.ru, например 7707329152-snr
    title: str
    payload_tag: str  # тег блока данных внутри Документа
    fields: dict[str, str] = field(default_factory=dict)  # атрибут XML -> наше имя
    numeric_fields: frozenset[str] = frozenset()
    bool_fields: frozenset[str] = frozenset()
    # Набор перечисляет ТОЛЬКО компании с признаком (например, только со спецрежимом).
    # Тогда отсутствие ИНН — значимый факт, но лишь при полной загрузке.
    absence_is_meaningful: bool = False

    @property
    def base_url(self) -> str:
        return f"https://file.nalog.ru/opendata/{self.slug}/"


SNR = DatasetSpec(
    code="snr",
    slug="7707329152-snr",
    title="Специальные налоговые режимы",
    payload_tag="СведСНР",
    fields={
        "ПризнУСН": "usn",
        "ПризнАУСН": "ausn",
        "ПризнЕСХН": "eshn",
        "ПризнСРП": "srp",
    },
    bool_fields=frozenset({"usn", "ausn", "eshn", "srp"}),
    # проверено на выгрузке от 25.06.2026: записей со всеми нулями нет,
    # набор содержит только компании, применяющие спецрежимы
    absence_is_meaningful=True,
)

REVEXP = DatasetSpec(
    code="revexp",
    slug="7707329152-revexp",
    title="Доходы и расходы по данным бухгалтерской отчётности",
    payload_tag="СведДохРасх",
    fields={"СумДоход": "revenue", "СумРасход": "expenses"},
    numeric_fields=frozenset({"revenue", "expenses"}),
)

SSHR = DatasetSpec(
    code="sshr",
    slug="7707329152-sshr2019",
    title="Среднесписочная численность работников",
    payload_tag="СведССЧР",
    fields={"КолРаб": "employees"},
    numeric_fields=frozenset({"employees"}),
)

PAYTAX = DatasetSpec(
    code="paytax",
    slug="7707329152-paytax",
    title="Уплаченные налоги и сборы",
    payload_tag="СведУплНал",
    fields={"НаимНалог": "tax_name", "СумНалог": "tax_sum"},
    numeric_fields=frozenset({"tax_sum"}),
)

DEBTAM = DatasetSpec(
    code="debtam",
    slug="7707329152-debtam",
    title="Задолженность по налогам и сборам",
    payload_tag="СведНедоим",
    fields={"НаимНалог": "tax_name", "СумНедоим": "debt", "СумШтраф": "penalty"},
    numeric_fields=frozenset({"debt", "penalty"}),
)

# Подключены и проверены на реальных выгрузках
ACTIVE_DATASETS: tuple[DatasetSpec, ...] = (SNR, REVEXP)

# Структура заявлена по документации ФНС, но на реальном файле ещё не сверена.
# Включать в ACTIVE_DATASETS только после проверки — иначе парсер молча даст пустоту.
UNVERIFIED_DATASETS: tuple[DatasetSpec, ...] = (SSHR, PAYTAX, DEBTAM)

BY_CODE = {d.code: d for d in ACTIVE_DATASETS + UNVERIFIED_DATASETS}
