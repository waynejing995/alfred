class ProviderError(Exception):
    """Base error for Alfred-owned provider failures."""


class ProviderAuthError(ProviderError):
    """Authentication or authorization failed."""


class ProviderRateLimit(ProviderError):
    """Provider rate limit was hit."""


class ProviderTimeout(ProviderError):
    """Provider call timed out."""


class ProviderUnavailable(ProviderError):
    """Provider is temporarily unavailable."""


class ProviderContextExceeded(ProviderError):
    """Request exceeded the provider context window."""


class ProviderBadRequest(ProviderError):
    """Provider rejected malformed request or response data."""


class ConfigError(Exception):
    """Provider/config construction failed before a model call."""

