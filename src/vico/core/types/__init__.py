"""
vico.core.types — core-layer primitive types.

Types owned here (core layer only):
  agent.py      — AgentState
  callbacks.py  — AgentCallbacks + callback type aliases
  messages.py   — Message, ContentBlock, TokenUsage, etc.

Configuration types (AgentConfig, LLMConfig, …) live in vico.config.types.
Tool / LLM / Skills types live in their respective subsystem packages.
"""

from __future__ import annotations

from vico.core.types.agent import AgentState
from vico.core.types.callbacks import (
    AgentCallbacks,
    ApprovalCallback,
    OnDoneCallback,
    OnErrorCallback,
    OnLoopCallback,
    OnSkillActivatedCallback,
    OnTextCallback,
    OnThinkingCallback,
    OnToolCallCallback,
    OnToolResultCallback,
)
from vico.core.types.messages import (
    ContentBlock,
    Message,
    MessageRole,
    TextBlock,
    TokenUsage,
    ToolResultBlock,
    ToolUseBlock,
)

__all__ = [
    # agent
    "AgentState",
    # callbacks
    "AgentCallbacks",
    "ApprovalCallback",
    "OnDoneCallback",
    "OnErrorCallback",
    "OnLoopCallback",
    "OnSkillActivatedCallback",
    "OnTextCallback",
    "OnThinkingCallback",
    "OnToolCallCallback",
    "OnToolResultCallback",
    # messages
    "ContentBlock",
    "Message",
    "MessageRole",
    "TextBlock",
    "TokenUsage",
    "ToolResultBlock",
    "ToolUseBlock",
]
