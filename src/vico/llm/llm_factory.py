"""
LLM Factory — creates the correct LLM instance from AgentConfig / LLMConfig.

Supported providers: deepseek, mimo (auto-discovered via PROVIDER_REGISTRY).

Adding a new provider:
  1. Create ``vico/llm/providers/<name>.py`` with a Config dataclass and LLM subclass.
  2. Call ``register_provider("name", ConfigClass, LLMClass)`` in that file.
  3. Add model metadata to ``vico/llm/models.py``.
"""

from __future__ import annotations

from typing import Any

from vico.config import DEFAULT_MAX_TOKENS
from vico.core.types import AgentConfig, LLMConfig
from vico.exceptions import ModelUnknownError, ProviderUnknownError
from vico.llm.base import LLM
from vico.llm.models import ALL_MODELS, PROVIDER_DEFAULTS

_PROVIDER_REGISTRY: dict[str, tuple[type[Any], type[LLM]]] = {}


def register_provider(
    provider_name: str,
    config_class: type[Any],
    llm_class: type[LLM],
) -> None:
    """Register a provider. Called by each provider module at import time."""
    _PROVIDER_REGISTRY[provider_name.lower()] = (config_class, llm_class)


def create_llm_from_config(config: AgentConfig | LLMConfig) -> LLM:
    """Create an LLM from AgentConfig or LLMConfig."""
    llm_config = config.llm if isinstance(config, AgentConfig) else config
    opts = llm_config.provider_options
    return _build_llm(
        provider=llm_config.provider,
        api_key=llm_config.api_key,
        base_url=llm_config.base_url,
        model=llm_config.model,
        max_tokens=llm_config.max_tokens,
        temperature=llm_config.temperature,
        top_p=llm_config.top_p,
        stop=llm_config.stop,
        thinking_enabled=llm_config.thinking_enabled,
        reasoning_effort=opts.get("reasoning_effort", "max"),
        response_format=opts.get("response_format", "text"),
    )


def _build_llm(
    provider: str,
    api_key: str,
    base_url: str,
    model: str,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = 1.0,
    top_p: float | None = None,
    stop: list[str] | None = None,
    thinking_enabled: bool = True,
    reasoning_effort: str = "max",
    response_format: str = "text",
) -> LLM:
    name = provider.lower()

    entry = _PROVIDER_REGISTRY.get(name)
    if not entry:
        raise ProviderUnknownError(
            f"Unknown LLM provider: {provider!r}. "
            f"Supported: {', '.join(sorted(_PROVIDER_REGISTRY.keys()))}"
        )
    config_class, llm_class = entry

    provider_models = ALL_MODELS.get(name, {})
    if model not in provider_models:
        raise ModelUnknownError(provider, model, list(provider_models.keys()))

    resolved_base_url = base_url or PROVIDER_DEFAULTS.get(name, {}).get("base_url", "")

    common_kwargs: dict[str, Any] = {
        "api_key": api_key,
        "base_url": resolved_base_url,
        "model": model,
        "temperature": temperature,
        "top_p": top_p,
        "stop": stop,
        "thinking_enabled": thinking_enabled,
        "response_format": response_format,
    }

    if name == "deepseek":
        common_kwargs["max_tokens"] = max_tokens
        common_kwargs["reasoning_effort"] = reasoning_effort
    elif name == "mimo":
        common_kwargs["max_completion_tokens"] = max_tokens

    provider_config = config_class(**common_kwargs)
    return llm_class(provider_config)  # type: ignore[call-arg]


# Auto-register built-in providers
from vico.llm.providers.deepseek import DeepSeekConfig, DeepSeekLLM  # noqa: F401, E402
from vico.llm.providers.mimo import MiMoConfig, MiMoLLM  # noqa: F401, E402

register_provider("deepseek", DeepSeekConfig, DeepSeekLLM)
register_provider("mimo", MiMoConfig, MiMoLLM)
