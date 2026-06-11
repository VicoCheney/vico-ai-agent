"""LLM request and model metadata types."""

from __future__ import annotations

from dataclasses import dataclass

from vico.core.types.messages import Message
from vico.tools.types.definition import ToolDefinition


@dataclass
class LLMRequest:
    system: str
    messages: list[Message]
    tools: list[ToolDefinition] | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    stop: list[str] | None = None
    thinking_enabled: bool | None = None
    reasoning_effort: str | None = None   # "high" | "max" (DeepSeek)
    response_format: str | None = None    # "text" | "json_object"


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
