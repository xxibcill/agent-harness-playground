from __future__ import annotations


class ProviderError(RuntimeError):
    """Base class for provider-related errors with actionable context."""

    def __init__(self, message: str, error_type: str, recovery_hint: str | None = None):
        super().__init__(message)
        self.error_type = error_type
        self.recovery_hint = recovery_hint


class ConfigurationError(RuntimeError):
    """Raised when workflow or provider configuration is invalid."""

    def __init__(self, message: str, config_field: str | None = None):
        super().__init__(message)
        self.config_field = config_field
