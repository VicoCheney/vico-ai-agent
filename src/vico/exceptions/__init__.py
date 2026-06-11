"""Custom exception hierarchy for Vico AI Agent."""

from __future__ import annotations


class VicoError(Exception):
    """Base exception for all Vico-specific errors."""


class ConfigError(VicoError):
    """Raised when configuration loading or validation fails."""


class ProviderError(VicoError):
    """Raised when an LLM provider communication or initialization fails."""


class ProviderAuthError(ProviderError):
    """Raised when authentication with an LLM provider fails (missing/invalid API key)."""


class ProviderUnknownError(ProviderError):
    """Raised when an unknown LLM provider is requested."""


class ToolExecutionError(VicoError):
    """Raised when a tool execution fails."""

    def __init__(self, tool_name: str, message: str, *, cause: Exception | None = None):
        self.tool_name = tool_name
        self.cause = cause
        super().__init__(f"Tool '{tool_name}' failed: {message}")


class ModelUnknownError(ProviderError):
    """Raised when an unknown model is requested for a provider."""

    def __init__(self, provider: str, model: str, available: list[str]):
        self.provider = provider
        self.model = model
        self.available = available
        super().__init__(
            f"Unknown model '{model}' for provider '{provider}'. "
            f"Available: {', '.join(available)}"
        )


# ─── Prompt subsystem errors ────────────────────────────────────────────────


class PromptError(VicoError):
    """Base class for prompt loading errors."""


class PromptFileNotFoundError(PromptError):
    """A required prompt file is missing from disk."""


class PromptValidationError(PromptError):
    """Prompt template or metadata is invalid."""


class PromptTemplateError(PromptError):
    """Jinja2 template rendering failed."""
