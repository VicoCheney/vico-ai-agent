"""Abstract base class for all tools.

Separated from ``vico.core.types`` so that the type-definition module
contains only pure data types (dataclasses and type aliases), while
behavioural contracts (abstract interfaces) live alongside their
concrete implementations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from vico.tools.types.definition import ToolDefinition
from vico.tools.types.execution import ToolExecutionContext, ToolResult, ToolRiskLevel


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
