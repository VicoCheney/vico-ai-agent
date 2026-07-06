"""
read — Read the contents of any file on disk.

Supports reading the full file or a specific line range (start_line / end_line).
Automatically truncates files that exceed MAX_CHARS and appends a notice so the
LLM knows to request a narrower range.

Risk level: LOW — read-only, always auto-approved.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from vico.tools.base import Tool
from vico.tools.path_safety import is_sensitive_path
from vico.tools.types.definition import (
    ToolDefinition,
    ToolParameterSchema,
)
from vico.tools.types.execution import (
    ToolExecutionContext,
    ToolResult,
    ToolRiskLevel,
)
from vico.utils.terminal import terminal_width as _terminal_width

MAX_CHARS = 40_000  # ~10K tokens max per read


class ReadTool(Tool):
    """Read file contents with optional line-range filtering. Risk level: low."""

    @property
    def risk_level(self) -> ToolRiskLevel:
        return "low"

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="read",
            description=(
                "Read the contents of a file. For large files, specify line ranges to avoid "
                "reading too much. Returns file content as text."
            ),
            parameters=ToolParameterSchema(
                type="object",
                properties={
                    "path": {
                        "type": "string",
                        "description": "Absolute or relative path to the file to read.",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "Optional: 1-indexed line number to start reading from.",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Optional: 1-indexed line number to stop reading at (inclusive).",
                    },
                },
                required=["path"],
            ),
        )

    async def execute(
        self,
        params: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        raw_path = str(params["path"])
        file_path = Path(os.path.join(context.cwd, raw_path)).resolve()

        cwd_path = Path(context.cwd).resolve()
        if not file_path.is_relative_to(cwd_path):
            return ToolResult(
                success=False,
                output="",
                error=(
                    f"Access denied: '{file_path}' is outside the working directory "
                    f"'{cwd_path}'. Only files within the project root can be read."
                ),
            )

        if is_sensitive_path(file_path):
            return ToolResult(
                success=False,
                output="",
                error=(
                    f"Refusing to read sensitive file '{file_path}'. "
                    "Read a sanitized example file instead, or ask the user to inspect it manually."
                ),
            )

        start_line: int | None = int(params["start_line"]) if "start_line" in params else None
        end_line: int | None = int(params["end_line"]) if "end_line" in params else None

        if not file_path.exists():
            return ToolResult(success=False, output="", error=f"File not found: {file_path}")

        if not file_path.is_file():
            return ToolResult(success=False, output="", error=f"Path is not a file: {file_path}")

        try:
            content = await asyncio.to_thread(file_path.read_text, encoding="utf-8", errors="replace")
        except OSError as exc:
            return ToolResult(success=False, output="", error=f"Cannot read file: {exc}")

        if start_line is not None or end_line is not None:
            lines = content.splitlines(keepends=True)
            from_idx = max(0, (start_line or 1) - 1)
            to_idx = end_line if end_line is not None else len(lines)
            content = "".join(lines[from_idx:to_idx])

        truncated = False
        if len(content) > MAX_CHARS:
            content = content[:MAX_CHARS]
            truncated = True

        range_info = ""
        if start_line or end_line:
            range_info = f" (lines {start_line or 1}–{end_line or 'end'})"

        header = f"File: {file_path}{range_info}\n{'─' * _terminal_width()}\n"
        footer = "\n\n[... file truncated. Use start_line/end_line to read more ...]" if truncated else ""

        return ToolResult(
            success=True,
            output=header + content + footer,
            metadata={"file_path": str(file_path), "truncated": truncated, "size": file_path.stat().st_size},
        )
