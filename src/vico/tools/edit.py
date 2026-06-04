"""
edit — Edit a file by replacing an exact piece of text with new content.

This is the primary tool for all file modifications. It performs a precise,
targeted edit: locate old_text in the file, replace it with new_text.

Key invariants:
  - old_text must match exactly (whitespace, indentation, line endings).
  - old_text must appear exactly once — not zero times, not more than once.
  - If old_text matches multiple times, supply more surrounding lines to
    make it unique.

Risk level: MEDIUM — modifies files on disk.
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


class EditTool(Tool):
    @property
    def risk_level(self) -> ToolRiskLevel:
        return "medium"

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="edit",
            description=(
                "Edit a file by replacing an exact piece of text with new content. "
                "Provide old_text (the exact text to find) and new_text (the replacement). "
                "old_text must appear exactly once in the file — if it matches multiple times, "
                "include more surrounding lines to make it unique. "
                "This is the primary tool for all file edits — always prefer it over bash commands like sed or awk."
            ),
            parameters=ToolParameterSchema(
                type="object",
                properties={
                    "path": {
                        "type": "string",
                        "description": "Absolute or relative path to the file to edit.",
                    },
                    "old_text": {
                        "type": "string",
                        "description": (
                            "The exact text to replace. Must match exactly including "
                            "whitespace and indentation. Must appear exactly once in the file."
                        ),
                    },
                    "new_text": {
                        "type": "string",
                        "description": "The new text to replace it with.",
                    },
                },
                required=["path", "old_text", "new_text"],
            ),
        )

    async def execute(
        self,
        params: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        raw_path = str(params["path"])
        file_path = Path(os.path.join(context.cwd, raw_path)).resolve()

        # Security: block path traversal
        cwd_path = Path(context.cwd).resolve()
        if not file_path.is_relative_to(cwd_path):
            return ToolResult(
                success=False,
                output="",
                error=(
                    f"Access denied: '{file_path}' is outside the working directory "
                    f"'{cwd_path}'. Only files within the project root can be edited."
                ),
            )

        old_text = str(params["old_text"])
        new_text = str(params["new_text"])

        if old_text == new_text:
            return ToolResult(
                success=False,
                output="",
                error="old_text and new_text are identical. Nothing to edit.",
            )

        if not old_text:
            return ToolResult(
                success=False,
                output="",
                error="old_text is empty. Provide the exact text you want to replace.",
            )

        if not file_path.exists():
            return ToolResult(success=False, output="", error=f"File not found: {file_path}")

        if not file_path.is_file():
            return ToolResult(success=False, output="", error=f"Path is not a file: {file_path}")

        try:
            original = await asyncio.to_thread(file_path.read_text, encoding="utf-8", errors="replace")
        except OSError as exc:
            return ToolResult(success=False, output="", error=f"Cannot read file: {exc}")

        count = original.count(old_text)
        if count == 0:
            # Try normalising line endings to help when the LLM uses \n vs \r\n
            normalised = original.replace("\r\n", "\n")
            count_norm = normalised.count(old_text)
            if count_norm == 0:
                return ToolResult(
                    success=False,
                    output="",
                    error=(
                        f"old_text not found in '{file_path}'. "
                        "Make sure it matches exactly (whitespace, indentation, line endings). "
                        "Re-read the relevant section of the file first if unsure."
                    ),
                )
            # Line endings were the mismatch — proceed with normalised content
            count = count_norm
            original = normalised

        if count > 1:
            return ToolResult(
                success=False,
                output="",
                error=(
                    f"old_text appears {count} times in '{file_path}'. "
                    "It must be unique. Add more surrounding context lines to old_text "
                    "so it matches exactly one location."
                ),
            )

        new_content = original.replace(old_text, new_text, 1)

        try:
            await asyncio.to_thread(file_path.write_text, new_content, encoding="utf-8")
        except OSError as exc:
            return ToolResult(success=False, output="", error=f"Cannot write file: {exc}")

        old_lines = old_text.count("\n") + 1
        new_lines = new_text.count("\n") + 1
        summary = f"✓ Edited '{file_path}'\n  Lines changed: {old_lines} → {new_lines}"

        return ToolResult(
            success=True,
            output=summary,
            metadata={
                "file_path": str(file_path),
                "old_lines": old_lines,
                "new_lines": new_lines,
            },
        )
