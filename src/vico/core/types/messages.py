"""Message and content-block types for the conversation history."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Literal

MessageRole = Literal["system", "user", "assistant", "tool"]


@dataclass
class TextBlock:
    type: Literal["text"] = "text"
    text: str = ""


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict  # type: ignore[type-arg]
    type: Literal["tool_use"] = "tool_use"


@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: str
    is_error: bool = False
    type: Literal["tool_result"] = "tool_result"


ContentBlock = TextBlock | ToolUseBlock | ToolResultBlock


@dataclass
class TokenUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass
class Message:
    role: MessageRole
    content: str | list[ContentBlock]
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: int = field(default_factory=lambda: time.time_ns() // 1_000_000)
    usage: TokenUsage | None = None
