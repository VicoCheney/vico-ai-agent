"""
LLM Factory
===========

Creates the correct LLM instance from AgentConfig / LLMConfig.

Supported providers:
  deepseek  — DeepSeek (https://api.deepseek.com)
  mimo      — Xiaomi MiMo (https://platform.xiaomimimo.com)
"""

from __future__ import annotations

from vico.core.types import LLM, AgentConfig, LLMConfig
from vico.llm.models import DEEPSEEK_MODELS, MIMO_MODELS
from vico.llm.providers.deepseek import DeepSeekConfig, DeepSeekLLM
from vico.llm.providers.mimo import MiMoConfig, MiMoLLM


def create_llm_from_config(config: AgentConfig | LLMConfig) -> LLM:
    """Create an LLM from AgentConfig or LLMConfig."""
    llm_config = config.llm if isinstance(config, AgentConfig) else config

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
        reasoning_effort=llm_config.reasoning_effort,
        response_format=llm_config.response_format,
    )


def _build_llm(
    provider: str,
    api_key: str,
    base_url: str,
    model: str,
    max_tokens: int = 131072,
    temperature: float = 1.0,
    top_p: float | None = None,
    stop: list[str] | None = None,
    thinking_enabled: bool = True,
    reasoning_effort: str = "max",
    response_format: str = "text",
) -> LLM:
    name = provider.lower()

    if name == "deepseek":
        # Validate model against registry (mirrors MiMo's fast-fail check)
        # so unknown model names are caught at startup instead of at first API call.
        if model not in DEEPSEEK_MODELS:
            available = ", ".join(DEEPSEEK_MODELS.keys())
            raise ValueError(f"Unknown DeepSeek model: {model!r}. Supported models: {available}")
        return DeepSeekLLM(
            DeepSeekConfig(
                api_key=api_key,
                base_url=base_url or "https://api.deepseek.com",
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                stop=stop,
                thinking_enabled=thinking_enabled,
                reasoning_effort=reasoning_effort,
                response_format=response_format,
            )
        )

    if name == "mimo":
        # Validate model against registry; fail fast if unknown.
        if model not in MIMO_MODELS:
            available = ", ".join(MIMO_MODELS.keys())
            raise ValueError(f"Unknown MiMo model: {model!r}. Supported models: {available}")
        return MiMoLLM(
            MiMoConfig(
                api_key=api_key,
                base_url=base_url or "https://token-plan-cn.xiaomimimo.com/v1",
                model=model,
                max_completion_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                stop=stop,
                thinking_enabled=thinking_enabled,
                response_format=response_format,
                # reasoning_effort is DeepSeek-only; MiMoConfig intentionally omits it
            )
        )

    raise ValueError(f"Unknown LLM provider: {provider!r}. Supported: deepseek, mimo")
