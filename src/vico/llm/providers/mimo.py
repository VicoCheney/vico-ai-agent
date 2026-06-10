"""
MiMo LLM Provider (Xiaomi, Token Plan, SGP cluster)
=====================================================

Endpoint: https://token-plan-sgp.xiaomimimo.com/v1

Supported models:
  mimo-v2.5-pro  — 1M ctx, 128K output, reasoning + tools
  mimo-v2.5      — 1M ctx, 128K output, reasoning + tools + vision (omni)

MiMo-specific behavior (vs DeepSeek):
  - Uses ``max_completion_tokens`` (includes reasoning tokens).
  - Sends ``thinking`` via ``extra_body``.
  - Does NOT support ``reasoning_effort``.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

import httpx
from openai import AsyncOpenAI

from vico.core.types import LLMRequest, ModelInfo, StreamChunk
from vico.llm.models import MIMO_MODELS, PROVIDER_DEFAULTS
from vico.llm.providers.base import _DEFAULT_TIMEOUT, OpenAICompatibleLLM

__all__ = ["MiMoConfig", "MiMoLLM"]


@dataclass
class MiMoConfig:
    api_key: str
    model: str = PROVIDER_DEFAULTS["mimo"]["default_model"]
    base_url: str = PROVIDER_DEFAULTS["mimo"]["base_url"]
    max_completion_tokens: int = 131072
    temperature: float = 1.0
    top_p: float | None = None
    stop: list[str] | None = field(default=None)
    thinking_enabled: bool = True
    response_format: str = "text"


class MiMoLLM(OpenAICompatibleLLM):
    """MiMo LLM provider."""

    def __init__(self, config: MiMoConfig) -> None:
        self._config = config
        self._client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            http_client=httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT),
        )

    @property
    def name(self) -> str:
        return "mimo"

    def get_model_info(self) -> ModelInfo | None:
        return MIMO_MODELS.get(self._config.model)

    def get_max_context_tokens(self) -> int:
        info = self.get_model_info()
        return info.max_context_tokens if info else 1_000_000

    def supports_tool_use(self) -> bool:
        return True

    def supports_vision(self) -> bool:
        info = self.get_model_info()
        return info.supports_vision if info else False

    async def stream(self, request: LLMRequest) -> AsyncGenerator[StreamChunk, None]:
        extra_body: dict[str, object] = {}
        if self._config.thinking_enabled:
            extra_body["thinking"] = {"type": "enabled"}

        async for chunk in self._stream_common(
            request,
            max_tokens_key="max_completion_tokens",
            max_tokens_value=self._config.max_completion_tokens,
            extra_body=extra_body,
        ):
            yield chunk
