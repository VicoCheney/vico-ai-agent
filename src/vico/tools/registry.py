"""Tool registry — registers and dispatches tool calls."""

from __future__ import annotations

from typing import Any

from vico.tools.base import Tool
from vico.tools.types.definition import ToolDefinition
from vico.tools.types.execution import ToolExecutionContext, ToolResult


class ToolRegistry:
    """Registry that maps tool names to ``Tool`` instances and dispatches calls."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a single tool instance (keyed by its definition name)."""
        self._tools[tool.definition.name] = tool

    def register_all(self, tools: list[Tool]) -> None:
        """Register a list of tool instances in one call."""
        for tool in tools:
            self.register(tool)

    def get(self, name: str) -> Tool | None:
        """Return the tool with the given name, or None if not registered."""
        return self._tools.get(name)

    def get_definitions(self) -> list[ToolDefinition]:
        """Return the JSON-schema definitions for all registered tools."""
        return [t.definition for t in self._tools.values()]

    def describe_tools(self) -> list[dict[str, str]]:
        """Return compact tool metadata for diagnostics."""
        return [
            {
                "name": tool.definition.name,
                "risk": tool.risk_level,
                "description": tool.definition.description.splitlines()[0],
            }
            for tool in self._tools.values()
        ]

    async def execute(
        self,
        name: str,
        params: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        """Dispatch a tool call by name. Returns an error ToolResult if unknown."""
        tool = self._tools.get(name)
        if not tool:
            return ToolResult(success=False, output="", error=f"Unknown tool: {name!r}")
        return await tool.execute(params, context)
