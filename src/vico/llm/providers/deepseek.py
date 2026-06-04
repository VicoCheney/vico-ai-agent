"""
DeepSeek LLM
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
from vico.llm.providers.base import OpenAICompatibleLLM


@dataclass
class DeepSeekConfig:
    """Configuration for DeepSeek provider (runtime parameters)."""
    api_key: str
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-flash"
    # ── Sampling ──────────────────────────────────────────────────────────────
    max_tokens: int = 131072
    temperature: float = 1.0
    top_p: float | None = None          # None = API default (1.0)
    stop: list[str] | None = None
    # ── Reasoning / thinking mode ─────────────────────────────────────────────
    thinking_enabled: bool = True       # enable chain-of-thought by default
    reasoning_effort: str = "max"       # "high" | "max"
    # ── Output format ─────────────────────────────────────────────────────────
    response_format: str = "text"       # "text" | "json_object"


class DeepSeekLLM(OpenAICompatibleLLM):
    """DeepSeek LLM using OpenAI-compatible streaming API."""

    def __init__(self, config: DeepSeekConfig) -> None:
        self._config = config
        self._client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
        )

    @property
    def name(self) -> str:
        return "deepseek"

    def get_max_context_tokens(self) -> int:
        return 128_000  # All V4 models: 128K context window

    def supports_tool_use(self) -> bool:
        return True

    def supports_vision(self) -> bool:
        return False

    async def stream(self, request: LLMRequest) -> AsyncIterator[StreamChunk]:  # type: ignore[override]
        messages = self._build_messages(request)
        tools = self._build_tools(request.tools) if self.supports_tool_use() else None

        # Per-request overrides take precedence over config defaults
        max_tokens = request.max_tokens or self._config.max_tokens
        temperature = request.temperature if request.temperature is not None else self._config.temperature
        top_p = request.top_p if request.top_p is not None else self._config.top_p
        stop = request.stop if request.stop is not None else self._config.stop

        thinking_enabled = (
            request.thinking_enabled
            if request.thinking_enabled is not None
            else self._config.thinking_enabled
        )
        reasoning_effort = request.reasoning_effort or self._config.reasoning_effort
        thinking_param = {"type": "enabled" if thinking_enabled else "disabled"}

        fmt = request.response_format or self._config.response_format
        response_format_param = {"type": fmt} if fmt != "text" else None

        kwargs: dict[str, Any] = {
            "model": self._config.model,
            "messages": messages,
            "stream": True,
            "max_tokens": max_tokens,
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

        # `thinking` and `reasoning_effort` are DeepSeek-specific fields —
        # pass via extra_body to avoid OpenAI SDK schema validation errors.
        extra_body: dict[str, Any] = {"thinking": thinking_param}
        if reasoning_effort:
            extra_body["reasoning_effort"] = reasoning_effort
        kwargs["extra_body"] = extra_body

        try:
            raw_stream = await self._client.chat.completions.create(**kwargs)  # type: ignore[arg-type]
        except Exception as exc:
            yield ErrorChunk(error=exc)
            return

        async for chunk in self._process_stream(raw_stream):
            yield chunk

    # _build_messages, _build_tools, _process_stream — inherited from OpenAICompatibleLLM
