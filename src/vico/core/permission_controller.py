"""
Permission Controller
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from vico.tools.registry import ToolRegistry
from vico.tools.types.call import ToolCall
from vico.tools.types.execution import ToolRiskLevel


@dataclass(frozen=True)
class ApprovalGrant:
    """A session approval for one normalized tool call shape."""

    tool_name: str
    input_fingerprint: str


class PermissionController:
    """Controls tool execution permissions."""

    def __init__(self, auto_approve_risks: list[ToolRiskLevel] | None = None) -> None:
        self._auto_approve_risks: set[ToolRiskLevel] = set(auto_approve_risks or ["low"])
        self._session_approvals: set[ApprovalGrant] = set()
        self._yolo_mode = False

    def is_auto_approved(self, tool_call: ToolCall, registry: ToolRegistry) -> bool:
        """Return True if this tool call can be executed without asking the user."""
        if self._yolo_mode:
            return True
        if self._grant_for(tool_call) in self._session_approvals:
            return True
        tool = registry.get(tool_call.name)
        if not tool:
            return False
        return tool.risk_level in self._auto_approve_risks

    def grant_session_approval(self, tool_call: ToolCall) -> None:
        """Remember that this exact normalized tool call is approved for the session."""
        self._session_approvals.add(self._grant_for(tool_call))

    def clear_session_approvals(self) -> None:
        """Clear exact-call session approvals without changing YOLO mode."""
        self._session_approvals.clear()

    def enable_yolo_mode(self) -> None:
        """Auto-approve every tool call for the rest of this process session."""
        self._yolo_mode = True

    def yolo_mode_enabled(self) -> bool:
        return self._yolo_mode

    def describe_session_approvals(self) -> list[dict[str, str]]:
        return [
            {"tool": grant.tool_name, "input_fingerprint": grant.input_fingerprint}
            for grant in sorted(
                self._session_approvals,
                key=lambda item: (item.tool_name, item.input_fingerprint),
            )
        ]

    @staticmethod
    def _grant_for(tool_call: ToolCall) -> ApprovalGrant:
        fingerprint = json.dumps(tool_call.input, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return ApprovalGrant(tool_name=tool_call.name, input_fingerprint=fingerprint)
