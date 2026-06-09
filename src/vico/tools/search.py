"""
search — Search for text patterns across files using regex.

Uses ripgrep (rg) when available, falls back to grep.
Returns matching lines with file paths and line numbers, capped at MAX_RESULTS
and MAX_OUTPUT_CHARS to protect the context window.

Risk level: LOW — read-only, always auto-approved.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
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

MAX_RESULTS = 50
MAX_OUTPUT_CHARS = 12_000

# Cache the ripgrep availability check at module load time — the result is
# stable for the lifetime of the process and shutil.which() does file-system
# lookups that are wasteful to repeat on every search call.
_RG_PATH: str | None = shutil.which("rg")


def _has_ripgrep() -> bool:
    return _RG_PATH is not None


class SearchTool(Tool):
    @property
    def risk_level(self) -> ToolRiskLevel:
        return "low"

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="search",
            description=(
                "Search for text patterns in files using regex (ripgrep). "
                f"Returns up to {MAX_RESULTS} matching lines with file paths and line numbers. "
                "Use this to find function definitions, usages, TODO comments, etc. "
                "If results overflow, narrow the pattern or add a file_pattern filter."
            ),
            parameters=ToolParameterSchema(
                type="object",
                properties={
                    "pattern": {
                        "type": "string",
                        "description": "Regular expression or text to search for.",
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "Optional: directory or file path to search in "
                            "(relative to cwd). Defaults to entire project."
                        ),
                    },
                    "file_pattern": {
                        "type": "string",
                        "description": 'Optional: glob pattern to filter files (e.g., "*.py", "*.ts").',
                    },
                    "case_sensitive": {
                        "type": "boolean",
                        "description": "Optional: whether the search is case-sensitive. Defaults to false.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": f"Optional: maximum number of results to return. Defaults to {MAX_RESULTS}.",
                    },
                },
                required=["pattern"],
            ),
        )

    async def execute(
        self,
        params: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        pattern = str(params["pattern"])
        search_path = (
            Path(os.path.join(context.cwd, str(params["path"]))).resolve() if "path" in params else Path(context.cwd)
        )
        file_pattern: str | None = str(params["file_pattern"]) if "file_pattern" in params else None
        case_sensitive = bool(params.get("case_sensitive", False))
        max_results = int(params.get("max_results", MAX_RESULTS))

        if not search_path.exists():
            return ToolResult(
                success=False,
                output="",
                error=f"Search path not found: {search_path}",
            )

        use_rg = _has_ripgrep()
        try:
            output = await self._run_search(
                pattern=pattern,
                search_path=str(search_path),
                file_pattern=file_pattern,
                case_sensitive=case_sensitive,
                max_results=max_results,
                use_rg=use_rg,
                cwd=context.cwd,
            )
        except subprocess.CalledProcessError as exc:
            # grep exits 1 when no matches — not an error
            if exc.returncode == 1 and not exc.stderr:
                output = ""
            else:
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Search failed: {exc.stderr or str(exc)}",
                )

        if not output.strip():
            return ToolResult(
                success=True,
                output=f"No matches found for pattern: {pattern}",
                metadata={"matches": 0, "pattern": pattern, "search_path": str(search_path)},
            )

        lines = output.strip().splitlines()
        match_count = len(lines)
        truncated = False
        display_output = output
        if len(display_output) > MAX_OUTPUT_CHARS:
            # Find a clean cut point
            tail = display_output.rfind("\n", MAX_OUTPUT_CHARS - 2000, MAX_OUTPUT_CHARS)
            if tail > 0:
                display_output = display_output[:tail]
            truncated = True

        # Recompute displayed line count after truncation so the header
        # accurately reflects how many results are shown.
        displayed_count = len(display_output.strip().splitlines()) if truncated else match_count

        file_info = f" in {file_pattern}" if file_pattern else ""
        trunc_info = f" (showing {displayed_count} of {match_count})" if truncated else ""
        header = f"Search: {pattern!r}{file_info} — {match_count} matches{trunc_info}\n{'─' * 60}\n"

        if truncated:
            hint = (
                f"\n\n[Output truncated at ~{MAX_OUTPUT_CHARS:,} characters. "
                "Refine your regex or restrict the file pattern to narrow results.]"
            )
            display_output += hint

        return ToolResult(
            success=True,
            output=header + display_output,
            metadata={
                "matches": match_count,
                "pattern": pattern,
                "search_path": str(search_path),
                "truncated": truncated,
                "tool": "ripgrep" if use_rg else "grep",
            },
        )

    async def _run_search(
        self,
        pattern: str,
        search_path: str,
        file_pattern: str | None,
        case_sensitive: bool,
        max_results: int,
        use_rg: bool,
        cwd: str,
    ) -> str:
        if use_rg:
            cmd = [
                "rg",
                "--line-number",
                "--with-filename",
                "--no-heading",
                f"--max-count={max_results}",
                "--color=never",
            ]
            if not case_sensitive:
                cmd.append("--ignore-case")
            if file_pattern:
                cmd.extend(["--glob", file_pattern])
            cmd.extend([pattern, search_path])
        else:
            # grep -m limits per-file; use head after to enforce a global cap.
            cmd = ["grep", "-rn"]
            if not case_sensitive:
                cmd.append("-i")
            if file_pattern:
                cmd.extend(["--include", file_pattern])
            cmd.extend([pattern, search_path])

        result = await asyncio.to_thread(
            subprocess.run,
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        raw_output = result.stdout

        # Enforce global max_results cap (rg/grep both apply limits per-file).
        lines = raw_output.splitlines(keepends=True)
        if len(lines) > max_results:
            lines = lines[:max_results]
        return "".join(lines)
