"""
LLM Factory — creates the correct LLM instance from AgentConfig / LLMConfig.

Supported providers: deepseek, mimo (auto-discovered via PROVIDER_REGISTRY).

Adding a new provider:
  1. Create ``vico/llm/providers/<name>.py`` with a Config dataclass and LLM subclass.
  2. Call ``register_provider("name", ConfigClass, LLMClass, kwargs_builder)``
     in that file, where ``kwargs_builder`` maps ``LLMConfig`` → provider-specific kwargs dict.
  3. Add model metadata to ``vico/llm/models.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from vico.config.types.config import DEFAULT_MAX_TOKENS, AgentConfig, LLMConfig
from vico.exceptions import ModelUnknownError, ProviderUnknownError
from vico.llm.base import LLM
from vico.llm.models import ALL_MODELS, PROVIDER_DEFAULTS

# Registry entry: (ConfigClass, LLMClass, kwargs_builder)
# kwargs_builder(llm_config) → dict of extra provider-specific kwargs
_ProviderEntry = tuple[
    type[Any],
    type[LLM],
    Callable[[LLMConfig], dict[str, Any]],
]
_PROVIDER_REGISTRY: dict[str, _ProviderEntry] = {}


def register_provider(
    provider_name: str,
    config_class: type[Any],
    llm_class: type[LLM],
    kwargs_builder: Callable[[LLMConfig], dict[str, Any]] | None = None,
) -> None:
    """Register a provider. Called by each provider module at import time.

    ``kwargs_builder`` receives the normalised ``LLMConfig`` and should return
    a dict of provider-specific constructor kwargs (e.g. ``max_tokens`` vs
    ``max_completion_tokens``).  Defaults to an empty-dict builder.
    """
    _PROVIDER_REGISTRY[provider_name.lower()] = (
        config_class,
        llm_class,
        kwargs_builder or (lambda _cfg: {}),
    )


def create_llm_from_config(config: AgentConfig | LLMConfig) -> LLM:
    """Create an LLM from AgentConfig or LLMConfig."""
    llm_config = config.llm if isinstance(config, AgentConfig) else config
    return _build_llm(llm_config)


def _build_llm(llm_config: LLMConfig) -> LLM:
    name = llm_config.provider.lower()

    entry = _PROVIDER_REGISTRY.get(name)
    if not entry:
        raise ProviderUnknownError(
            f"Unknown LLM provider: {llm_config.provider!r}. "
            f"Supported: {', '.join(sorted(_PROVIDER_REGISTRY.keys()))}"
        )
    config_class, llm_class, kwargs_builder = entry

    provider_models = ALL_MODELS.get(name, {})
    if llm_config.model not in provider_models:
        raise ModelUnknownError(name, llm_config.model, list(provider_models.keys()))

    resolved_base_url = llm_config.base_url or PROVIDER_DEFAULTS.get(name, {}).get("base_url", "")
    opts = llm_config.provider_options

    # Common kwargs shared by all providers
    common_kwargs: dict[str, Any] = {
        "api_key": llm_config.api_key,
        "base_url": resolved_base_url,
        "model": llm_config.model,
        "temperature": llm_config.temperature,
        "top_p": llm_config.top_p,
        "stop": llm_config.stop,
        "thinking_enabled": llm_config.thinking_enabled,
        "response_format": opts.get("response_format", "text"),
    }

    # Provider-specific kwargs supplied by the provider's own kwargs_builder
    extra = kwargs_builder(llm_config)
    common_kwargs.update(extra)

    provider_config = config_class(**common_kwargs)
    return llm_class(provider_config)  # type: ignore[call-arg]


# Auto-register built-in providers
from vico.llm.providers.deepseek import DeepSeekConfig, DeepSeekLLM  # noqa: F401, E402
from vico.llm.providers.mimo import MiMoConfig, MiMoLLM  # noqa: F401, E402


def _deepseek_kwargs(cfg: LLMConfig) -> dict[str, Any]:
    return {
        "max_tokens": cfg.max_tokens or DEFAULT_MAX_TOKENS,
        "reasoning_effort": cfg.provider_options.get("reasoning_effort", "max"),
    }


def _mimo_kwargs(cfg: LLMConfig) -> dict[str, Any]:
    return {"max_completion_tokens": cfg.max_tokens or DEFAULT_MAX_TOKENS}


register_provider("deepseek", DeepSeekConfig, DeepSeekLLM, _deepseek_kwargs)
register_provider("mimo", MiMoConfig, MiMoLLM, _mimo_kwargs)
