"""
Core type definitions for Vico AI Agent

These dataclasses define the foundational contracts shared by all modules.
"""

from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from asyncio import Event
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

# ─────────────────────────────────────────────────────────────────────────────
# Message Types
# ─────────────────────────────────────────────────────────────────────────────

MessageRole = Literal["system", "user", "assistant", "tool"]


@dataclass
class TextBlock:
    type: Literal["text"] = "text"
    text: str = ""


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]
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
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    timestamp: int = field(default_factory=lambda: time.time_ns() // 1_000_000)
    usage: TokenUsage | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Tool Types
# ─────────────────────────────────────────────────────────────────────────────

ToolRiskLevel = Literal["low", "medium", "high"]


@dataclass
class ToolParameterSchema:
    type: str
    properties: dict[str, Any]
    required: list[str] = field(default_factory=list)
    additional_properties: bool = False

    def to_dict(self) -> dict[str, Any]:
        schema: dict[str, Any] = {
            "type": self.type,
            "properties": self.properties,
        }
        if self.required:
            schema["required"] = self.required
        if not self.additional_properties:
            schema["additionalProperties"] = False
        return schema


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: ToolParameterSchema

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters.to_dict(),
            },
        }


@dataclass
class ToolExecutionContext:
    cwd: str
    env: dict[str, str] = field(default_factory=dict)
    cancel_event: Event = field(default_factory=Event)

    @property
    def cancelled(self) -> bool:
        """True if the agent has been cancelled."""
        return self.cancel_event.is_set()


@dataclass
class ToolResult:
    success: bool
    output: str
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class Tool(ABC):
    """Abstract base class for all tools."""

    @property
    @abstractmethod
    def definition(self) -> ToolDefinition: ...

    @property
    @abstractmethod
    def risk_level(self) -> ToolRiskLevel: ...

    @abstractmethod
    async def execute(
        self,
        params: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult: ...


# ─────────────────────────────────────────────────────────────────────────────
# Tool Call / Stream Chunk Types
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


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


# ─────────────────────────────────────────────────────────────────────────────
# LLM Provider Interface
# ─────────────────────────────────────────────────────────────────────────────


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
    reasoning_effort: str | None = None  # "high" | "max" (DeepSeek)
    response_format: str | None = None  # "text" | "json_object"


class LLM(ABC):
    """Abstract base class for all LLM instances (provider + model + config)."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def stream(self, request: LLMRequest) -> AsyncIterator[StreamChunk]: ...

    @abstractmethod
    def get_max_context_tokens(self) -> int: ...

    @abstractmethod
    def supports_tool_use(self) -> bool: ...

    @abstractmethod
    def supports_vision(self) -> bool: ...


# ─────────────────────────────────────────────────────────────────────────────
# Agent Config
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class LLMConfig:
    provider: str = "mimo"
    api_key: str = ""
    base_url: str = "https://api.xiaomimimo.com/v1"
    model: str = "mimo-v2.5-pro"
    max_tokens: int = 131072
    temperature: float = 1.0
    top_p: float | None = None
    stop: list[str] | None = None
    thinking_enabled: bool = True
    reasoning_effort: str = "max"  # DeepSeek: "high" | "max"
    response_format: str = "text"  # "text" | "json_object"


@dataclass
class ContextConfig:
    max_tokens: int = 60000
    reserve_tokens: int = 4096
    compression_threshold: float = 0.85


@dataclass
class ToolsConfig:
    auto_approve: list[ToolRiskLevel] = field(default_factory=lambda: ["low"])
    timeout_ms: int = 30000


@dataclass
class AgentConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    cwd: str = field(default_factory=lambda: __import__("os").getcwd())


# ─────────────────────────────────────────────────────────────────────────────
# Agent State
# ─────────────────────────────────────────────────────────────────────────────

AgentState = Literal["idle", "running", "waiting_approval", "error", "done"]
