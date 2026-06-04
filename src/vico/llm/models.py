"""
Model registry — defines available models per provider.

Each model is described by its name, max context window, and capabilities.
This is the single source of truth for "what models exist and what can they do".

Supported providers
-------------------
  deepseek  — DeepSeek (https://api.deepseek.com)
  mimo      — Xiaomi MiMo (https://platform.xiaomimimo.com)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ModelInfo:
    """Static metadata about a specific model."""

    name: str
    display_name: str
    max_context_tokens: int
    max_output_tokens: int
    supports_tool_use: bool = True
    supports_vision: bool = False
    supports_reasoning: bool = False


# ─── DeepSeek models ─────────────────────────────────────────────────────────

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
    # Legacy aliases (deprecated 2026/07/24)
    "deepseek-chat": ModelInfo(
        name="deepseek-chat",
        display_name="DeepSeek Chat (legacy)",
        max_context_tokens=128_000,
        max_output_tokens=8_192,
    ),
    "deepseek-reasoner": ModelInfo(
        name="deepseek-reasoner",
        display_name="DeepSeek Reasoner (legacy)",
        max_context_tokens=128_000,
        max_output_tokens=8_192,
        supports_reasoning=True,
    ),
}

# ─── MiMo models (v2.5 series, Token Plan) ────────────────────────────────────
#
# Docs: https://platform.xiaomimimo.com/docs/zh-CN/quick-start/model
#
# mimo-v2.5-pro   — Pro series: 1M ctx, 128K output, text + deep thinking + tools
# mimo-v2.5       — Omni series: 1M ctx, 128K output, multimodal + deep thinking + tools

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
        supports_vision=True,  # Omni: supports image / audio / video input
        supports_reasoning=True,
    ),
}

# ─── Registry ─────────────────────────────────────────────────────────────────

ALL_MODELS: dict[str, dict[str, ModelInfo]] = {
    "deepseek": DEEPSEEK_MODELS,
    "mimo": MIMO_MODELS,
}


def get_model_info(provider: str, model_name: str) -> ModelInfo | None:
    """Look up model metadata. Returns None if not found."""
    provider_models = ALL_MODELS.get(provider)
    if not provider_models:
        return None
    return provider_models.get(model_name)
