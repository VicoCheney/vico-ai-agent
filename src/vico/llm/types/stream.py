"""Streaming chunk types emitted by LLM providers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from vico.core.types.messages import TokenUsage
from vico.tools.types.call import ToolCall


@dataclass
class TextChunk:
    type: Literal["text"] = "text"
    content: str = ""


@dataclass
class ReasoningChunk:
    type: Literal["reasoning"] = "reasoning"
    content: str = ""


@dataclass
class ToolCallChunk:
    tool_call: ToolCall
    type: Literal["tool_call"] = "tool_call"


@dataclass
class DoneChunk:
    type: Literal["done"] = "done"
    usage: TokenUsage | None = None
    stop_reason: str | None = None


@dataclass
class ErrorChunk:
    error: Exception
    type: Literal["error"] = "error"


StreamChunk = TextChunk | ReasoningChunk | ToolCallChunk | DoneChunk | ErrorChunk
