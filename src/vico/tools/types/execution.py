"""Tool execution context and result types."""

from __future__ import annotations

from asyncio import Event
from dataclasses import dataclass, field
from typing import Any, Literal

ToolRiskLevel = Literal["low", "medium", "high"]


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
