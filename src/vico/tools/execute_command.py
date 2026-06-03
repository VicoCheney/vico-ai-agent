"""
execute_command — Run shell commands in the current working directory

Risk level: HIGH — always requires user approval (or explicit override).
Streams stdout/stderr together and returns combined output.
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
    Tool,
    ToolDefinition,
    ToolExecutionContext,
    ToolParameterSchema,
    ToolResult,
    ToolRiskLevel,
)

MAX_OUTPUT_CHARS = 30_000

# Commands that may request interactive TTY input (read /dev/tty directly and will hang).
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


def _is_interactive_command(command: str) -> tuple[bool, str]:
    """Return (True, reason) if the command is likely to block waiting for TTY input."""
    normalized = command.strip()
    for pat in _INTERACTIVE_PATTERNS:
        if pat.search(normalized):
            return True, f"Command contains a pattern matching '{pat.pattern}' which may require interactive TTY input (password prompt). Use a non-privileged or non-interactive alternative."
    return False, ""


def _kill_process_tree(process: asyncio.subprocess.Process) -> None:
    """Kill the process and its entire process group to avoid orphan children."""
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


class ExecuteCommandTool(Tool):
    @property
    def risk_level(self) -> ToolRiskLevel:
        return "high"

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="execute_command",
            description=(
                "Execute a shell command in the working directory. "
                "Use this to run scripts, install packages, run tests, build projects, "
                "or any other shell operation. Returns combined stdout and stderr output."
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
                        f"'{project_root}'. Only directories within the project root are allowed."
                    ),
                )
            cwd = str(cwd_path)
        else:
            cwd = context.cwd

        timeout_ms = int(params.get("timeout_ms", 30_000))
        timeout_sec = timeout_ms / 1000.0

        is_interactive, reason = _is_interactive_command(command)
        if is_interactive:
            return ToolResult(
                success=False,
                output="",
                error=(
                    f"Command blocked: {reason}  "
                    "Tip: use a non-privileged alternative (e.g. read system "
                    "info via sysctl/sw_vers/system_profiler rather than sudo)."
                ),
            )

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
                # start_new_session=True gives the shell a dedicated process group
                # so os.killpg() kills the whole tree on timeout or cancellation.
                start_new_session=True,
            )
        except OSError as exc:
            return ToolResult(
                success=False,
                output="",
                error=f"Failed to spawn command: {exc}",
            )

        output_chunks: list[str] = []
        timed_out = False

        communicate_task = asyncio.create_task(process.communicate())
        cancel_event = context.cancel_event

        try:
            done, _ = await asyncio.wait(
                {communicate_task, asyncio.create_task(cancel_event.wait())},
                timeout=timeout_sec,
                return_when=asyncio.FIRST_COMPLETED,
            )

            if not done:
                timed_out = True
                _kill_process_tree(process)
                communicate_task.cancel()
                try:
                    await communicate_task
                except (asyncio.CancelledError, Exception):
                    pass
            elif cancel_event.is_set() and not communicate_task.done():
                _kill_process_tree(process)
                communicate_task.cancel()
                try:
                    await communicate_task
                except (asyncio.CancelledError, Exception):
                    pass
            else:
                stdout, _ = communicate_task.result()
                output_chunks.append(stdout.decode(errors="replace") if stdout else "")

        except Exception:
            _kill_process_tree(process)
            communicate_task.cancel()

        if context.cancelled and not timed_out:
            return ToolResult(success=False, output="", error="Cancelled during execution.")

        output = "".join(output_chunks)
        truncated = False
        if len(output) > MAX_OUTPUT_CHARS:
            output = output[:MAX_OUTPUT_CHARS]
            truncated = True

        exit_code = process.returncode if process.returncode is not None else -1
        header = f"$ {command}\n{'─' * 60}\n"
        if truncated:
            footer = "\n[... output truncated ...]"
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
