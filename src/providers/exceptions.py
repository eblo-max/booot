class ProviderError(Exception):
    """Базовая ошибка источника данных."""


class ProviderUnavailable(ProviderError):
    """Источник не отвечает / 5xx / таймаут. Повторяем позже."""


class ProviderRateLimited(ProviderError):
    """429 или исчерпан лимит. retry_after — через сколько секунд можно повторить."""

    def __init__(self, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class ProviderAuthError(ProviderError):
    """Неверный или истёкший ключ. Повторять бессмысленно — нужен человек."""


class ProviderQuotaExceeded(ProviderError):
    """Годовой лимит тарифа исчерпан."""


class MassSearchNotSupported(ProviderError):
    """Провайдер умеет только точечные запросы по ИНН/ОГРН.

    Поднимается вместо имитации поиска. Бот честно сообщает об этом пользователю
    и предлагает импорт Excel/CSV.
    """
