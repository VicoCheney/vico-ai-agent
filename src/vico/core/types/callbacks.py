"""Agent callback type aliases and the AgentCallbacks container."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from vico.skills.types.meta import SkillMeta
    from vico.tools.types.call import ToolCall
    from vico.tools.types.execution import ToolResult

OnThinkingCallback = Callable[[str], None]
OnTextCallback = Callable[[str], None]
OnToolCallCallback = Callable[["ToolCall"], None]
OnToolResultCallback = Callable[["ToolCall", "ToolResult"], None]
OnErrorCallback = Callable[[Exception], None]
OnDoneCallback = Callable[[int, int], None]   # prompt_tokens, completion_tokens
OnLoopCallback = Callable[[int], None]
OnSkillActivatedCallback = Callable[["SkillMeta"], None]
ApprovalCallback = Callable[
    ["ToolCall"],
    "Coroutine[Any, Any, Literal['approve', 'approve_always', 'deny']]",
]


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
