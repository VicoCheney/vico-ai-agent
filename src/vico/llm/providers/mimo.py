"""
MiMo LLM Provider
=================

Implements the LLM interface using Xiaomi MiMo's OpenAI-compatible API.
Endpoint: https://token-plan-sgp.xiaomimimo.com/v1  (Token Plan, SGP cluster)

Provider-specific behaviour vs the shared base:
  - Uses `max_completion_tokens` (not `max_tokens`) — includes reasoning tokens.
  - Sends `thinking` via `extra_body` (MiMo-specific field).
  - Does NOT support `reasoning_effort` (DeepSeek-only).

Token Plan base URLs (OpenAI-compatible):
  CN:  https://token-plan-cn.xiaomimimo.com/v1
  SGP: https://token-plan-sgp.xiaomimimo.com/v1   ← default
  AMS: https://token-plan-ams.xiaomimimo.com/v1

Supported models (v2.5 series):
  mimo-v2.5-pro  — 1M ctx, 128K output, reasoning + tools
  mimo-v2.5      — 1M ctx, 128K output, reasoning + tools + vision (omni)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI

from vico.core.types import (
    ErrorChunk,
    LLMRequest,
    StreamChunk,
)
from vico.llm.models import MIMO_MODELS
from vico.llm.providers.base import OpenAICompatibleLLM

_MIMO_BASE_URL = "https://token-plan-cn.xiaomimimo.com/v1"


@dataclass
class MiMoConfig:
    """Runtime configuration for the MiMo provider."""

    api_key: str
    base_url: str = _MIMO_BASE_URL
    model: str = "mimo-v2.5-pro"
    # ── Sampling ──────────────────────────────────────────────────────────────
    max_completion_tokens: int = 131072
    temperature: float = 1.0  # MiMo pro/omni default is 1.0
    top_p: float | None = None  # None = API default (0.95 for MiMo)
    stop: list[str] | None = None
    # ── Reasoning / thinking mode ─────────────────────────────────────────────
    thinking_enabled: bool = True  # maps to thinking.type = "enabled"/"disabled"
    # (MiMo does NOT support reasoning_effort — that's DeepSeek-only)
    # ── Output format ─────────────────────────────────────────────────────────
    response_format: str = "text"  # "text" | "json_object"


class MiMoLLM(OpenAICompatibleLLM):
    """
    Xiaomi MiMo LLM using OpenAI-compatible streaming API.

    Reasoning (deep thinking) is enabled by default on mimo-v2.5-pro and
    mimo-v2.5. The `reasoning_content` delta field carries the thinking text,
    handled by the shared _process_stream() in the base class.
    """

    def __init__(self, config: MiMoConfig) -> None:
        self._config = config
        self._client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
        )

    @property
    def name(self) -> str:
        return "mimo"

    def get_max_context_tokens(self) -> int:
        info = MIMO_MODELS.get(self._config.model)
        return info.max_context_tokens if info else 1_000_000

    def supports_tool_use(self) -> bool:
        return True

    def supports_vision(self) -> bool:
        info = MIMO_MODELS.get(self._config.model)
        return info.supports_vision if info else False

    async def stream(self, request: LLMRequest) -> AsyncIterator[StreamChunk]:
        messages = self._build_messages(request)
        tools = self._build_tools(request.tools) if self.supports_tool_use() else None

        # MiMo uses `max_completion_tokens` (includes reasoning tokens in budget)
        max_completion_tokens = request.max_tokens or self._config.max_completion_tokens

        # Per-request overrides take precedence over config defaults
        temperature = request.temperature if request.temperature is not None else self._config.temperature
        top_p = request.top_p if request.top_p is not None else self._config.top_p
        stop = request.stop if request.stop is not None else self._config.stop

        thinking_enabled = (
            request.thinking_enabled if request.thinking_enabled is not None else self._config.thinking_enabled
        )
        thinking_param = {"type": "enabled" if thinking_enabled else "disabled"}

        fmt = request.response_format or self._config.response_format
        response_format_param = {"type": fmt} if fmt != "text" else None

        kwargs: dict[str, Any] = {
            "model": self._config.model,
            "messages": messages,
            "stream": True,
            "max_completion_tokens": max_completion_tokens,
            "temperature": temperature,
        }
        if top_p is not None:
            kwargs["top_p"] = top_p
        if stop:
            kwargs["stop"] = stop
        if response_format_param:
            kwargs["response_format"] = response_format_param
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        # `thinking` is MiMo-specific — pass via extra_body to avoid SDK schema errors.
        kwargs["extra_body"] = {"thinking": thinking_param}

        try:
            raw_stream = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            yield ErrorChunk(error=exc)
            return

        async for chunk in self._process_stream(raw_stream):
            yield chunk

    # _build_messages, _build_tools, _process_stream — inherited from OpenAICompatibleLLM
