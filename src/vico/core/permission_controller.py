"""
Permission Controller

Decides whether a tool call needs user approval before execution.
Supports "remember for session" to avoid repeated prompting.

Risk matrix:
  - low:    always auto-approve (read, search)
  - medium: ask once, remember for session
  - high:   always ask
"""

from __future__ import annotations

from vico.core.types import ToolCall, ToolRiskLevel
from vico.tools.registry import ToolRegistry


class PermissionController:
    """Controls tool execution permissions."""

    def __init__(self, auto_approve_risks: list[ToolRiskLevel] | None = None) -> None:
        self._auto_approve_risks: set[ToolRiskLevel] = set(auto_approve_risks or ["low"])
        self._session_approvals: set[str] = set()

    def is_auto_approved(self, tool_call: ToolCall, registry: ToolRegistry) -> bool:
        """Return True if this tool call can be executed without asking the user."""
        if tool_call.name in self._session_approvals:
            return True
        tool = registry.get(tool_call.name)
        if not tool:
            return False
        return tool.risk_level in self._auto_approve_risks

    def grant_session_approval(self, tool_name: str) -> None:
        """Remember that this tool is approved for the rest of the session."""
        self._session_approvals.add(tool_name)

    def clear_session_approvals(self) -> None:
        """Clear all session-level approvals (e.g. when switching models)."""
        self._session_approvals.clear()
