"""Base classes for OpenAI-compatible LLM providers.

``OpenAICompatibleLLM`` provides shared utilities for providers using the
OpenAI Python SDK (AsyncOpenAI) against an OpenAI-compatible endpoint:
  - _build_messages()  — internal Message format → OpenAI dict format
  - _build_tools()     — ToolDefinition list → OpenAI tools format
  - _process_stream()  — consume raw AsyncOpenAI stream, yield StreamChunks
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

import httpx
from openai import AsyncOpenAI

from vico.core.types import (
    TextBlock,
    TokenUsage,
    ToolResultBlock,
)
from vico.llm.base import LLM
from vico.llm.types.request import LLMRequest
from vico.llm.types.stream import (
    DoneChunk,
    ErrorChunk,
    ReasoningChunk,
    StreamChunk,
    TextChunk,
    ToolCallChunk,
)
from vico.tools.types.call import ToolCall
from vico.tools.types.definition import ToolDefinition

__all__ = ["LLM", "OpenAICompatibleLLM"]

_logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_BACKOFF_BASE_S = 1.0
_RETRYABLE_STATUS_CODES = {429, 502, 503}

_DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)


def _parse_usage(usage: Any) -> TokenUsage | None:
    if not usage:
        return None
    try:
        return TokenUsage(
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            total_tokens=getattr(usage, "total_tokens", 0) or 0,
        )
    except Exception as exc:
        _logger.warning("_parse_usage: failed to parse %r: %s", usage, exc)
        return None


class OpenAICompatibleLLM(LLM):
    """Mixin base for providers using an OpenAI-compatible endpoint.

    Concrete subclasses must implement:
      name, get_max_context_tokens(), supports_tool_use(), supports_vision(), stream()
    """

    _client: AsyncOpenAI
    _config: Any

    async def aclose(self) -> None:
        await self._client.close()

    @staticmethod
    def _build_messages(request: LLMRequest) -> list[dict[str, Any]]:
        """Convert internal Message list → OpenAI-compatible messages array."""
        result: list[dict[str, Any]] = []
        result.append({"role": "system", "content": request.system})

        for msg in request.messages:
            if msg.role == "system":
                note = msg.content if isinstance(msg.content, str) else ""
                if note:
                    result[0]["content"] = f"{result[0]['content']}\n\n{note}"

            elif msg.role == "user":
                if isinstance(msg.content, str):
                    content = msg.content
                else:
                    parts: list[str] = []
                    for b in msg.content:
                        if isinstance(b, TextBlock):
                            parts.append(b.text)
                        elif isinstance(b, ToolResultBlock):
                            parts.append(b.content)
                        else:
                            _logger.warning(
                                "_build_messages: unknown user content block %s, skipping",
                                type(b).__name__,
                            )
                    content = "".join(parts)
                result.append({"role": "user", "content": content})

            elif msg.role == "assistant":
                if isinstance(msg.content, str):
                    result.append({"role": "assistant", "content": msg.content})
                else:
                    text_parts = [b.text for b in msg.content if b.type == "text"]
                    tool_uses = [b for b in msg.content if b.type == "tool_use"]

                    if tool_uses:
                        result.append(
                            {
                                "role": "assistant",
                                "content": "".join(text_parts) or None,
                                "tool_calls": [
                                    {
                                        "id": tc.id,
                                        "type": "function",
                                        "function": {
                                            "name": tc.name,
                                            "arguments": json.dumps(tc.input),
                                        },
                                    }
                                    for tc in tool_uses
                                ],
                            }
                        )
                    else:
                        result.append({"role": "assistant", "content": "".join(text_parts)})

            elif msg.role == "tool":
                if isinstance(msg.content, list):
                    for block in msg.content:
                        if block.type == "tool_result":
                            result.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": block.tool_use_id,
                                    "content": block.content,
                                }
                            )

        return result

    @staticmethod
    def _build_tools(tools: list[ToolDefinition] | None) -> list[dict[str, Any]] | None:
        if not tools:
            return None
        return [t.to_dict() for t in tools]

    async def _stream_common(
        self,
        request: LLMRequest,
        *,
        max_tokens_key: str,
        max_tokens_value: int,
        extra_body: dict[str, Any],
    ) -> AsyncGenerator[StreamChunk, None]:
        """Build API kwargs and stream the response with exponential-backoff retry."""
        messages = self._build_messages(request)
        tools = self._build_tools(request.tools) if self.supports_tool_use() else None

        kwargs: dict[str, Any] = {
            "model": self._config.model,
            "messages": messages,
            "stream": True,
            max_tokens_key: max_tokens_value,
            "temperature": request.temperature if request.temperature is not None else 1.0,
        }
        if request.top_p is not None:
            kwargs["top_p"] = request.top_p
        if request.stop:
            kwargs["stop"] = request.stop
        if request.response_format and request.response_format != "text":
            kwargs["response_format"] = {"type": request.response_format}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if extra_body:
            kwargs["extra_body"] = extra_body

        for attempt in range(_MAX_RETRIES + 1):
            try:
                raw_stream = await self._client.chat.completions.create(**kwargs)
                break
            except Exception as exc:
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                is_retryable = (
                    status_code in _RETRYABLE_STATUS_CODES
                    or isinstance(exc, (httpx.ConnectError, httpx.ReadTimeout))
                )
                if not is_retryable or attempt >= _MAX_RETRIES:
                    yield ErrorChunk(error=exc)
                    return
                delay = _BACKOFF_BASE_S * (2 ** attempt)
                _logger.warning(
                    "LLM API %s (status=%s), retrying in %.1fs (%d/%d)",
                    type(exc).__name__, status_code, delay, attempt + 1, _MAX_RETRIES,
                )
                await asyncio.sleep(delay)

        async for chunk in self._process_stream(raw_stream):
            yield chunk

    @staticmethod
    def _flush_pending_tool_calls(
        pending_tool_calls: dict[int, dict[str, str]],
    ) -> list[ToolCallChunk]:
        """Parse and yield ToolCallChunks from buffered streaming fragments."""
        chunks: list[ToolCallChunk] = []
        for tc_data in pending_tool_calls.values():
            try:
                tool_input = json.loads(tc_data["args_buffer"] or "{}")
            except json.JSONDecodeError:
                tool_input = {"_raw": tc_data["args_buffer"]}
            chunks.append(
                ToolCallChunk(
                    tool_call=ToolCall(
                        id=tc_data["id"],
                        name=tc_data["name"],
                        input=tool_input,
                    )
                )
            )
        pending_tool_calls.clear()
        return chunks

    @staticmethod
    async def _process_stream(raw_stream: Any) -> AsyncGenerator[StreamChunk, None]:
        """Consume a raw AsyncOpenAI streaming response and yield StreamChunks.

        Handles reasoning_content, delta.content, delta.tool_calls, and usage.
        Usage may arrive in a trailing empty-choices chunk (e.g. MiMo).
        """
        pending_tool_calls: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        deferred_usage: Any = None

        async for chunk in raw_stream:
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage is not None:
                deferred_usage = chunk_usage

            choice = chunk.choices[0] if chunk.choices else None
            if not choice:
                # Trailing usage-only chunk (choices=[]).
                if finish_reason is not None:
                    yield DoneChunk(stop_reason=finish_reason, usage=_parse_usage(deferred_usage))
                    finish_reason = None
                continue

            delta = choice.delta

            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                yield ReasoningChunk(content=reasoning)

            if delta.content:
                yield TextChunk(content=delta.content)

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in pending_tool_calls:
                        pending_tool_calls[idx] = {"id": "", "name": "", "args_buffer": ""}
                    pending = pending_tool_calls[idx]
                    if tc.id:
                        pending["id"] = tc.id
                    if tc.function and tc.function.name:
                        pending["name"] = tc.function.name
                    if tc.function and tc.function.arguments:
                        pending["args_buffer"] += tc.function.arguments

            if choice.finish_reason is not None and pending_tool_calls:
                for chunk in OpenAICompatibleLLM._flush_pending_tool_calls(pending_tool_calls):
                    yield chunk

            if choice.finish_reason is not None:
                if deferred_usage is not None:
                    yield DoneChunk(stop_reason=choice.finish_reason, usage=_parse_usage(deferred_usage))
                    finish_reason = None
                else:
                    finish_reason = choice.finish_reason

        # Flush any dangling tool calls (non-compliant provider, no finish_reason).
        if pending_tool_calls:
            for chunk in OpenAICompatibleLLM._flush_pending_tool_calls(pending_tool_calls):
                yield chunk

        if finish_reason is not None:
            yield DoneChunk(stop_reason=finish_reason, usage=_parse_usage(deferred_usage))
