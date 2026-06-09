"""
write — Create a new file or overwrite an existing file with given content.

This is the primary tool for writing complete file content in one shot.
Use it when:
  - Creating a new file that does not yet exist
  - Rewriting an entire file from scratch (e.g. generating a game, config, script)

For targeted, surgical edits to an existing file use the `edit` tool instead.

Key behaviours:
  - Creates parent directories automatically if they do not exist.
  - If the file already exists it is OVERWRITTEN without confirmation — callers
    must ensure this is intentional (the agent's approval layer handles consent).
  - Writes UTF-8 text; binary files are not supported.

Risk level: MEDIUM — creates or overwrites files on disk.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from vico.core.types import (
    Tool,
    ToolDefinition,
    ToolExecutionContext,
    ToolParameterSchema,
    ToolResult,
    ToolRiskLevel,
)


class WriteTool(Tool):
    @property
    def risk_level(self) -> ToolRiskLevel:
        return "medium"

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="write",
            description=(
                "Create a new file or completely overwrite an existing file with the given content. "
                "Use this when you need to write an entire file at once — new scripts, HTML pages, "
                "config files, etc. "
                "For targeted edits to an existing file (replacing a specific snippet) use `edit` instead. "
                "Parent directories are created automatically."
            ),
            parameters=ToolParameterSchema(
                type="object",
                properties={
                    "path": {
                        "type": "string",
                        "description": (
                            "Path to write, relative to the current working directory. "
                            "Writing outside the working directory is rejected for safety."
                        ),
                    },
                    "content": {
                        "type": "string",
                        "description": "The full text content to write into the file.",
                    },
                },
                required=["path", "content"],
            ),
        )

    async def execute(
        self,
        params: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        raw_path = str(params["path"])
        file_path = Path(os.path.join(context.cwd, raw_path)).resolve()

        # Block path traversal — keep consistent with read/edit tools.
        # os.path.join discards `cwd` when raw_path is absolute, so the
        # resolve() above will land outside cwd if the LLM passes /etc/...
        cwd_path = Path(context.cwd).resolve()
        if not file_path.is_relative_to(cwd_path):
            return ToolResult(
                success=False,
                output="",
                error=(
                    f"Access denied: '{file_path}' is outside the working directory "
                    f"'{cwd_path}'. Only files within the project root can be written."
                ),
            )

        content = str(params["content"])

        existed = file_path.exists()

        # Create parent directories if needed.
        try:
            await asyncio.to_thread(file_path.parent.mkdir, parents=True, exist_ok=True)
        except OSError as exc:
            return ToolResult(
                success=False,
                output="",
                error=f"Cannot create parent directories for '{file_path}': {exc}",
            )

        try:
            await asyncio.to_thread(file_path.write_text, content, encoding="utf-8")
        except OSError as exc:
            return ToolResult(
                success=False,
                output="",
                error=f"Cannot write file '{file_path}': {exc}",
            )

        # Empty content: treat as 0 lines (not the ambiguous "1 line for empty file").
        lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        action = "Overwritten" if existed else "Created"
        summary = f"✓ {action} '{file_path}' ({lines} lines, {len(content.encode())} bytes)"

        return ToolResult(
            success=True,
            output=summary,
            metadata={
                "file_path": str(file_path),
                "action": action.lower(),
                "lines": lines,
                "bytes": len(content.encode()),
            },
        )
