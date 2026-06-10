"""
Model registry — defines available models per provider.

Supported providers: deepseek, mimo
"""

from __future__ import annotations

from typing import Any

from vico.core.types import ModelInfo

# Provider defaults — base_url and default model for each provider.
PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = {
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-v4-flash",
        "max_tokens_key": "max_tokens",
    },
    "mimo": {
        # SGP node is the only supported endpoint.
        "base_url": "https://token-plan-sgp.xiaomimimo.com/v1",
        "default_model": "mimo-v2.5-pro",
        "max_tokens_key": "max_completion_tokens",
    },
}

DEEPSEEK_MODELS: dict[str, ModelInfo] = {
    "deepseek-v4-flash": ModelInfo(
        name="deepseek-v4-flash",
        display_name="DeepSeek V4 Flash",
        max_context_tokens=128_000,
        max_output_tokens=8_192,
        supports_reasoning=True,
    ),
    "deepseek-v4-pro": ModelInfo(
        name="deepseek-v4-pro",
        display_name="DeepSeek V4 Pro",
        max_context_tokens=128_000,
        max_output_tokens=8_192,
        supports_reasoning=True,
    ),
}

# MiMo v2.5 series (Token Plan, SGP cluster)
# Docs: https://platform.xiaomimimo.com/docs/zh-CN/quick-start/model
MIMO_MODELS: dict[str, ModelInfo] = {
    "mimo-v2.5-pro": ModelInfo(
        name="mimo-v2.5-pro",
        display_name="MiMo V2.5 Pro",
        max_context_tokens=1_000_000,
        max_output_tokens=131_072,
        supports_vision=False,
        supports_reasoning=True,
    ),
    "mimo-v2.5": ModelInfo(
        name="mimo-v2.5",
        display_name="MiMo V2.5",
        max_context_tokens=1_000_000,
        max_output_tokens=131_072,
        supports_vision=True,
        supports_reasoning=True,
    ),
}

ALL_MODELS: dict[str, dict[str, ModelInfo]] = {
    "deepseek": DEEPSEEK_MODELS,
    "mimo": MIMO_MODELS,
}
