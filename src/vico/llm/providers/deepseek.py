"""
DeepSeek LLM Provider
=====================

Endpoint: https://api.deepseek.com

Supported models:
  deepseek-v4-flash  — 128K ctx, 8K output, reasoning
  deepseek-v4-pro    — 128K ctx, 8K output, reasoning

DeepSeek-specific behavior (vs MiMo):
  - Uses ``max_tokens`` (output tokens only, not including reasoning).
  - Supports ``reasoning_effort`` (``"high"`` | ``"max"``).
  - Does NOT accept ``thinking`` in ``extra_body``.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

import httpx
from openai import AsyncOpenAI

from vico.llm.models import DEEPSEEK_MODELS, PROVIDER_DEFAULTS
from vico.llm.providers.base import _DEFAULT_TIMEOUT, OpenAICompatibleLLM
from vico.llm.types.request import LLMRequest, ModelInfo
from vico.llm.types.stream import StreamChunk

__all__ = ["DeepSeekConfig", "DeepSeekLLM"]


@dataclass
class DeepSeekConfig:
    api_key: str
    model: str = PROVIDER_DEFAULTS["deepseek"]["default_model"]
    base_url: str = PROVIDER_DEFAULTS["deepseek"]["base_url"]
    max_tokens: int = 8192
    temperature: float = 1.0
    top_p: float | None = None
    stop: list[str] | None = field(default=None)
    thinking_enabled: bool = True
    reasoning_effort: str = "max"
    response_format: str = "text"


class DeepSeekLLM(OpenAICompatibleLLM):
    """DeepSeek LLM provider."""

    def __init__(self, config: DeepSeekConfig) -> None:
        self._config = config
        self._client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            http_client=httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT),
        )

    @property
    def name(self) -> str:
        return "deepseek"

    def get_model_info(self) -> ModelInfo | None:
        return DEEPSEEK_MODELS.get(self._config.model)

    def get_max_context_tokens(self) -> int:
        info = self.get_model_info()
        return info.max_context_tokens if info else 128_000

    def supports_tool_use(self) -> bool:
        return True

    def supports_vision(self) -> bool:
        return False

    async def stream(self, request: LLMRequest) -> AsyncGenerator[StreamChunk, None]:
        extra_body: dict[str, object] = {}
        if self._config.thinking_enabled and self._config.reasoning_effort:
            extra_body["reasoning_effort"] = self._config.reasoning_effort

        async for chunk in self._stream_common(
            request,
            max_tokens_key="max_tokens",
            max_tokens_value=self._config.max_tokens,
            extra_body=extra_body,
        ):
            yield chunk
