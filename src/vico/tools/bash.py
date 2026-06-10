"""
bash — Execute a shell command in the working directory.

Returns combined stdout+stderr. Enforces a hard output truncation limit
to prevent large output from blowing up the context window.

Safety:
  - Blocks commands requiring interactive TTY input (sudo, ssh, passwd, etc.)
  - Runs each command in its own process group for clean kill on timeout/cancel.

Risk level: HIGH — requires user approval by default.
"""

from __future__ import annotations

import asyncio
import os
import re
import signal
import sys
from pathlib import Path
from typing import Any

from vico.core.types import (
    ToolDefinition,
    ToolExecutionContext,
    ToolParameterSchema,
    ToolResult,
    ToolRiskLevel,
)
from vico.tools.base import Tool
from vico.utils.terminal import terminal_width as _terminal_width

MAX_OUTPUT_CHARS = 30_000
_READ_CHUNK_SIZE = 65_536  # 64 KiB — bounds memory while streaming output

# Commands that read /dev/tty directly and will hang waiting for user input.
_INTERACTIVE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bsudo\b"),
    re.compile(r"\bsu\b"),
    re.compile(r"\bssh\b"),
    re.compile(r"\bgpg\b"),
    re.compile(r"\bpasswd\b"),
    re.compile(r"\bchpasswd\b"),
    re.compile(r"\bopenvpn\b"),
    re.compile(r"\bmysql\b.*-p(?!\S)"),
    re.compile(r"\bpsql\b.*-p(?!\S)"),
]

# Shell wrappers that can smuggle interactive commands past word-boundary checks.
_WRAPPER_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"""(?:^|[\s;&|])(?:bash|sh|zsh|dash|ksh)\s+-c\s+(['"])(.*?)\1""", re.DOTALL),
    re.compile(r"""\beval\s+(['"])(.*?)\1""", re.DOTALL),
    re.compile(r"\$\((.*?)\)", re.DOTALL),
    re.compile(r"`([^`]*)`"),
]


def _expand_wrappers(command: str, depth: int = 0) -> str:
    """Recursively extract the payload from shell wrappers (depth-limited to 4)."""
    if depth > 4:
        return command
    expanded = command
    for pat in _WRAPPER_PATTERNS:

        def _repl(m: re.Match[str]) -> str:
            inner = m.group(2) if m.lastindex and m.lastindex >= 2 else m.group(1)
            return " " + _expand_wrappers(inner, depth + 1) + " "

        expanded = pat.sub(_repl, expanded)
    return expanded


def _is_interactive_command(command: str) -> tuple[bool, str]:
    """Return (True, reason) if the command would block waiting for TTY input."""
    haystack = _expand_wrappers(command.strip())
    for pat in _INTERACTIVE_PATTERNS:
        if pat.search(haystack):
            return (
                True,
                f"Command matches '{pat.pattern}' which may require interactive TTY input.",
            )
    return False, ""


def _kill_process_tree(process: asyncio.subprocess.Process) -> None:
    """Kill the process and its entire process group to avoid orphaned children."""
    try:
        if sys.platform != "win32":
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        else:
            process.kill()
    except (ProcessLookupError, PermissionError, OSError):
        try:
            process.kill()
        except Exception:
            pass


class BashTool(Tool):
    """Execute a shell command and return combined stdout + stderr. Risk level: high."""

    @property
    def risk_level(self) -> ToolRiskLevel:
        return "high"

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="bash",
            description=(
                "Execute a shell command. Returns combined stdout and stderr.\n"
                "NEVER use `sudo` — it will be blocked (no password input possible). "
                "Use non-privileged alternatives instead. If root is truly required, "
                "tell the user to run it manually."
            ),
            parameters=ToolParameterSchema(
                type="object",
                properties={
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute.",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Optional: working directory override (relative to project root).",
                    },
                    "timeout_ms": {
                        "type": "integer",
                        "description": "Optional: timeout in milliseconds (default: 30000).",
                    },
                },
                required=["command"],
            ),
        )

    async def execute(
        self,
        params: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        command = str(params["command"])

        if "cwd" in params:
            cwd_path = Path(os.path.join(context.cwd, str(params["cwd"]))).resolve()
            project_root = Path(context.cwd).resolve()
            if not cwd_path.is_relative_to(project_root):
                return ToolResult(
                    success=False,
                    output="",
                    error=(
                        f"Access denied: cwd '{cwd_path}' is outside the project root "
                        f"'{project_root}'."
                    ),
                )
            cwd = str(cwd_path)
        else:
            cwd = context.cwd

        timeout_ms = int(params.get("timeout_ms", 30_000))
        timeout_sec = timeout_ms / 1000.0

        is_interactive, reason = _is_interactive_command(command)
        if is_interactive:
            if re.search(r"\bsudo\b", command):
                error = (
                    "BLOCKED: `sudo` is not allowed (cannot accept password input). "
                    "Use a non-privileged alternative, or ask the user to run it manually."
                )
            else:
                error = f"BLOCKED: {reason} Use a non-interactive alternative instead."
            return ToolResult(success=False, output="", error=error)

        if context.cancelled:
            return ToolResult(success=False, output="", error="Cancelled before execution.")

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=cwd,
                env={**os.environ, **context.env},
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                # Dedicated process group for clean whole-tree kill.
                start_new_session=True,
            )
        except OSError as exc:
            return ToolResult(
                success=False,
                output="",
                error=f"Failed to spawn command: {exc}",
            )

        # Stream stdout in chunks to bound memory usage.
        # Race read_task vs cancel_waiter_task vs timeout.
        output_chunks: list[str] = []
        timed_out = False
        output_bytes = 0

        cancel_event = context.cancel_event

        async def _read_stream() -> None:
            nonlocal output_bytes
            assert process.stdout is not None
            while True:
                chunk = await process.stdout.read(_READ_CHUNK_SIZE)
                if not chunk:
                    break
                decoded = chunk.decode(errors="replace")
                output_chunks.append(decoded)
                output_bytes += len(decoded)
                if output_bytes >= MAX_OUTPUT_CHARS:
                    _kill_process_tree(process)
                    break

        read_task = asyncio.create_task(_read_stream())
        cancel_waiter_task = asyncio.create_task(cancel_event.wait())

        try:
            done, _ = await asyncio.wait(
                {read_task, cancel_waiter_task},
                timeout=timeout_sec,
                return_when=asyncio.FIRST_COMPLETED,
            )

            if not cancel_waiter_task.done():
                cancel_waiter_task.cancel()
                try:
                    await cancel_waiter_task
                except (asyncio.CancelledError, Exception):
                    pass

            if not done:
                timed_out = True
                _kill_process_tree(process)
                read_task.cancel()
                try:
                    await read_task
                except (asyncio.CancelledError, Exception):
                    pass
            elif cancel_event.is_set() and not read_task.done():
                _kill_process_tree(process)
                read_task.cancel()
                try:
                    await read_task
                except (asyncio.CancelledError, Exception):
                    pass
            else:
                try:
                    await read_task
                except (asyncio.CancelledError, Exception):
                    pass

        except Exception:
            _kill_process_tree(process)
            read_task.cancel()
            if not cancel_waiter_task.done():
                cancel_waiter_task.cancel()

        try:
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except (TimeoutError, Exception):
            _kill_process_tree(process)

        if context.cancelled and not timed_out:
            return ToolResult(success=False, output="", error="Cancelled during execution.")

        output = "".join(output_chunks)
        truncated = len(output) >= MAX_OUTPUT_CHARS
        if truncated:
            output = output[:MAX_OUTPUT_CHARS]

        exit_code = process.returncode if process.returncode is not None else -1
        header = f"$ {command}\n{'─' * _terminal_width()}\n"
        if truncated:
            tail = output.rfind("\n", MAX_OUTPUT_CHARS - 2000, MAX_OUTPUT_CHARS)
            if tail > 0:
                output = output[:tail]
            footer = (
                f"\n\n[Output truncated at {MAX_OUTPUT_CHARS:,} characters. "
                "Use a more specific command (e.g. head/tail/grep) to see the rest.]"
            )
        elif timed_out:
            footer = f"\n[Command timed out after {timeout_ms}ms]"
        else:
            footer = f"\n[Exit code: {exit_code}]"

        return ToolResult(
            success=(exit_code == 0 and not timed_out),
            output=header + output + footer,
            error=f"Command exited with code {exit_code}" if exit_code != 0 and not timed_out else None,
            metadata={"exit_code": exit_code, "timed_out": timed_out, "command": command, "cwd": cwd},
        )
