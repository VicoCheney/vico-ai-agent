"""
Base classes for OpenAI-compatible LLM providers.

`OpenAICompatibleLLM` provides shared utilities for providers that use the
OpenAI Python SDK (AsyncOpenAI) against an OpenAI-compatible endpoint.

Shared logic:
  - _build_messages()  — convert internal Message format → OpenAI dict format
  - _build_tools()     — convert ToolDefinition list → OpenAI tools format
  - _process_stream()  — consume a raw AsyncOpenAI stream, yield StreamChunks
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from vico.core.types import (
    LLM,
    DoneChunk,
    LLMRequest,
    ReasoningChunk,
    StreamChunk,
    TextBlock,
    TextChunk,
    TokenUsage,
    ToolCall,
    ToolCallChunk,
    ToolDefinition,
    ToolResultBlock,
)

__all__ = ["LLM", "OpenAICompatibleLLM"]

_logger = logging.getLogger(__name__)


def _parse_usage(usage: Any) -> TokenUsage | None:
    """Safely construct a TokenUsage from a raw API usage object."""
    if not usage:
        return None
    try:
        return TokenUsage(
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            total_tokens=getattr(usage, "total_tokens", 0) or 0,
        )
    except Exception as exc:
        _logger.warning("_parse_usage: failed to parse usage object %r: %s", usage, exc)
        return None


class OpenAICompatibleLLM(LLM):
    """
    Mixin base for providers that talk to an OpenAI-compatible endpoint.

    Concrete subclasses must still implement:
      - name (property)
      - get_max_context_tokens()
      - supports_tool_use()
      - supports_vision()
      - stream()

    They get for free:
      - _build_messages()
      - _build_tools()
      - _process_stream()
    """

    # ─── Shared message / tool builders ──────────────────────────────────────

    @staticmethod
    def _build_messages(request: LLMRequest) -> list[dict[str, Any]]:
        """Convert internal Message list → OpenAI-compatible messages array."""
        result: list[dict[str, Any]] = []
        result.append({"role": "system", "content": request.system})

        for msg in request.messages:
            if msg.role == "user":
                # Use isinstance checks to avoid silently dropping unknown block types
                # (hasattr duck-typing can collide when a block has both "text"/"content").
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
                                "_build_messages: unknown user content block type %s, skipping",
                                type(b).__name__,
                            )
                    content = "".join(parts)
                result.append({"role": "user", "content": content})

            elif msg.role == "assistant":
                if isinstance(msg.content, str):
                    result.append({"role": "assistant", "content": msg.content})
                else:
                    text_parts = [b.text for b in msg.content if b.type == "text"]  # noqa: E501
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
        """Convert ToolDefinition list → OpenAI tools array."""
        if not tools:
            return None
        return [t.to_dict() for t in tools]

    # ─── Shared stream processor ──────────────────────────────────────────────

    @staticmethod
    def _flush_pending_tool_calls(
        pending_tool_calls: dict[int, dict[str, str]],
    ) -> list[ToolCallChunk]:
        """Parse and yield ToolCallChunks from buffered streaming tool-call fragments.

        Called when a finish_reason arrives or the stream ends without one,
        so that partially-streamed tool calls are always emitted exactly once.
        """
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
        """
        Consume a raw AsyncOpenAI streaming response and yield StreamChunks.

        Handles reasoning_content, delta.content, delta.tool_calls, and usage.
        Usage may arrive in a trailing empty-choices chunk (e.g. MiMo), so we
        track finish_reason and usage separately and emit DoneChunk only after
        both are available.
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
                    yield DoneChunk(
                        stop_reason=finish_reason,
                        usage=_parse_usage(deferred_usage),
                    )
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

            # Flush accumulated tool calls on any terminal finish_reason.
            if choice.finish_reason is not None and pending_tool_calls:
                for chunk in OpenAICompatibleLLM._flush_pending_tool_calls(pending_tool_calls):
                    yield chunk

            if choice.finish_reason is not None:
                if deferred_usage is not None:
                    yield DoneChunk(
                        stop_reason=choice.finish_reason,
                        usage=_parse_usage(deferred_usage),
                    )
                    finish_reason = None
                else:
                    finish_reason = choice.finish_reason

        # Stream ended — flush any pending tool calls a non-compliant provider
        # may have left dangling (no finish_reason ever arrived).
        if pending_tool_calls:
            for chunk in OpenAICompatibleLLM._flush_pending_tool_calls(pending_tool_calls):
                yield chunk

        # Stream ended — emit DoneChunk if finish_reason was set without a trailing chunk.
        if finish_reason is not None:
            yield DoneChunk(
                stop_reason=finish_reason,
                usage=_parse_usage(deferred_usage),
            )
