"""Agent runtime state type."""

from __future__ import annotations

from typing import Literal

AgentState = Literal["idle", "running", "waiting_approval", "error", "done"]
