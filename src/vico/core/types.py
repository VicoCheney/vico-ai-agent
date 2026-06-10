"""
Core type definitions for Vico AI Agent

These dataclasses define the foundational contracts shared by all modules.
"""

from __future__ import annotations

import os
import time
import uuid
from asyncio import Event
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path
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
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
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


# ─────────────────────────────────────────────────────────────────────────────
# Agent Config
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class LLMConfig:
    provider: str = "mimo"
    api_key: str = ""
    base_url: str = "https://token-plan-sgp.xiaomimimo.com/v1"
    model: str = "mimo-v2.5-pro"
    max_tokens: int = 131072  # matches DEFAULT_MAX_TOKENS in config.py
    temperature: float = 1.0
    top_p: float | None = None
    stop: list[str] | None = None
    thinking_enabled: bool = True
    # Provider-specific parameters (e.g. reasoning_effort for DeepSeek,
    # response_format for any provider that supports it).
    # Kept out of the top-level fields to avoid polluting the generic config
    # with vendor-specific knobs that don't apply to every LLM.
    provider_options: dict[str, Any] = field(default_factory=dict)


@dataclass
class ContextConfig:
    max_tokens: int = 60000
    reserve_tokens: int = 4096
    compression_threshold: float = 0.85


@dataclass
class ToolsConfig:
    auto_approve: list[ToolRiskLevel] = field(default_factory=lambda: ["low"])
    timeout_ms: int = 30000
    # Environment variable pass-through to child processes.
    # When non-empty, only listed variables (and VICO_* prefixed ones) are passed.
    # When empty (default), the full os.environ is passed (legacy behaviour).
    # Users should set this in .vicorc.json for production use.
    env_whitelist: list[str] = field(default_factory=list)


@dataclass
class AgentLimits:
    """Configurable safety limits for the agent loop."""

    max_iterations: int = 30  # Maximum tool-use iterations per user message


@dataclass
class AgentConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    limits: AgentLimits = field(default_factory=AgentLimits)
    cwd: str = field(default_factory=os.getcwd)


# ─────────────────────────────────────────────────────────────────────────────
# Agent State
# ─────────────────────────────────────────────────────────────────────────────

AgentState = Literal["idle", "running", "waiting_approval", "error", "done"]


# ─────────────────────────────────────────────────────────────────────────────
# Skill Types
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class SkillMeta:
    """Parsed from SKILL.md frontmatter — lightweight, always in memory."""

    skill_id: str            # Directory name, used as unique identifier
    name: str                # Human-readable display name
    description: str         # Short description for model discovery (shown in system prompt JSON)
    argument_hint: str = ""  # Shown in /skills list, e.g. "[file-or-dir]"
    disable_model_invocation: bool = False  # If True, model cannot self-activate this skill
    user_invocable: bool = True             # If False, hidden from /skills list
    skill_dir: Path = field(default_factory=Path)


@dataclass
class SkillContent:
    """Full skill content — loaded on demand when a skill is activated."""

    meta: SkillMeta
    body: str  # Everything in SKILL.md below the frontmatter delimiter


# ─────────────────────────────────────────────────────────────────────────────
# LLM Provider Config Types
# ─────────────────────────────────────────────────────────────────────────────


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


# ─────────────────────────────────────────────────────────────────────────────
# Context Stats
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ContextStats:
    message_count: int
    estimated_tokens: int
    max_tokens: int
    usage_percent: float
    is_near_limit: bool


# ─────────────────────────────────────────────────────────────────────────────
# Agent Callback Types
# ─────────────────────────────────────────────────────────────────────────────

OnThinkingCallback = Callable[[str], None]
OnTextCallback = Callable[[str], None]
OnToolCallCallback = Callable[["ToolCall"], None]
OnToolResultCallback = Callable[["ToolCall", "ToolResult"], None]
OnErrorCallback = Callable[[Exception], None]
OnDoneCallback = Callable[[int, int], None]   # prompt_tokens, completion_tokens
OnLoopCallback = Callable[[int], None]
OnSkillActivatedCallback = Callable[["SkillMeta"], None]
ApprovalCallback = Callable[["ToolCall"], "Coroutine[Any, Any, Literal['approve', 'approve_always', 'deny']]"]


@dataclass
class AgentCallbacks:
    """All event callbacks from the agent loop to the UI."""

    on_thinking: OnThinkingCallback | None = None
    on_text: OnTextCallback | None = None
    on_tool_call: OnToolCallCallback | None = None
    on_tool_result: OnToolResultCallback | None = None
    on_error: OnErrorCallback | None = None
    on_done: OnDoneCallback | None = None
    on_loop: OnLoopCallback | None = None
    on_skill_activated: OnSkillActivatedCallback | None = None
    request_approval: ApprovalCallback | None = None
