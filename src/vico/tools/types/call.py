"""ToolCall — the identifier + input payload sent by the LLM."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]
