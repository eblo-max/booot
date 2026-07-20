from src.config import settings
from src.providers.base import CompanyProvider
from src.providers.fake import FakeProvider


def build_primary_provider() -> CompanyProvider:
    """Провайдер массового поиска. Выбор — переменной PRIMARY_PROVIDER."""
    name = settings.primary_provider.lower()

    if name == "fake":
        return FakeProvider()

    if name == "excel":
        from src.providers.excel_provider import ExcelProvider

        return ExcelProvider()

    if name == "datanewton":
        if not settings.datanewton_api_key:
            raise RuntimeError("PRIMARY_PROVIDER=datanewton, но DATANEWTON_API_KEY пуст")
        from src.providers.datanewton import DataNewtonProvider

        return DataNewtonProvider(settings.datanewton_api_key)

    if name == "checko":
        if not settings.checko_api_key:
            raise RuntimeError("PRIMARY_PROVIDER=checko, но CHECKO_API_KEY пуст")
        from src.providers.checko import CheckoProvider

        return CheckoProvider(settings.checko_api_key)

    raise RuntimeError(f"Неизвестный PRIMARY_PROVIDER: {settings.primary_provider}")


def build_lookup_provider() -> CompanyProvider:
    """Провайдер для точечных запросов по ИНН/ОГРН (/company, «Проверить повторно»).

    Массовый поиск может быть недоступен по тарифу, но карточка по одному ИНН
    работает — поэтому источник здесь выбирается отдельно от PRIMARY_PROVIDER.
    """
    if settings.datanewton_api_key:
        from src.providers.datanewton import DataNewtonProvider

        return DataNewtonProvider(settings.datanewton_api_key)

    if settings.checko_api_key:
        from src.providers.checko import CheckoProvider

        return CheckoProvider(settings.checko_api_key)

    return build_primary_provider()
