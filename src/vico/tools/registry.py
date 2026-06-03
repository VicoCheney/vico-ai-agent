"""Tool registry — registers and dispatches tool calls."""

from __future__ import annotations

from typing import Any

from vico.core.types import Tool, ToolDefinition, ToolExecutionContext, ToolResult


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.definition.name] = tool

    def register_all(self, tools: list[Tool]) -> None:
        for tool in tools:
            self.register(tool)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def get_definitions(self) -> list[ToolDefinition]:
        return [t.definition for t in self._tools.values()]

    async def execute(
        self,
        name: str,
        params: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        tool = self._tools.get(name)
        if not tool:
            return ToolResult(success=False, output="", error=f"Unknown tool: {name!r}")
        return await tool.execute(params, context)
