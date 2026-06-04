"""
bash — Execute a shell command in the working directory.

Returns combined stdout+stderr output. Enforces a hard output truncation limit
so large command output does not blow up the context window.

Safety:
  - Blocks commands that require interactive TTY input (sudo, ssh, passwd, etc.)
    as they will hang the agent waiting for a password or keystroke.
  - Runs each command in its own process group so the entire tree can be killed
    cleanly on timeout or cancellation.

Risk level: HIGH — can modify system state; requires user approval by default.
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
            return (
                True,
                f"Command contains a pattern matching '{pat.pattern}' which may require interactive TTY input (password prompt). Use a non-privileged or non-interactive alternative.",
            )
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


class BashTool(Tool):
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
            # Provide actionable guidance so the LLM can self-degrade:
            #   - Try a non-privileged alternative command
            #   - Or ask the user to run the command manually in their terminal
            if re.search(r"\bsudo\b", command):
                error = (
                    "BLOCKED: `sudo` is not allowed (cannot accept password input). "
                    "Use a non-privileged alternative, or ask the user to run it manually."
                )
            else:
                error = (
                    f"BLOCKED: {reason} "
                    "Use a non-interactive alternative instead."
                )
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

        # ── Streaming read with OOM guard ────────────────────────────────────
        # Instead of communicate() (which buffers all output in memory before
        # returning), we read stdout in small chunks and kill the process as
        # soon as the output budget is exhausted.  This prevents a runaway
        # command (e.g. `cat /dev/urandom`, `yes`, a 5 GB log grep) from
        # silently inflating the agent's RSS until the OS OOM-kills it.
        #
        # Concurrency model:
        #   read_task   — streams stdout chunks until EOF or budget exceeded
        #   cancel_waiter_task — fires when context.cancel_event is set
        #   asyncio.wait(timeout=…) — enforces the caller-supplied timeout
        #
        # All three are raced with asyncio.wait(FIRST_COMPLETED); whichever
        # wins causes the other two to be cancelled and the process tree to
        # be killed.
        # ─────────────────────────────────────────────────────────────────────
        _READ_CHUNK = 65_536  # 64 KiB per read syscall

        output_chunks: list[str] = []
        timed_out = False
        output_bytes = 0

        cancel_event = context.cancel_event

        async def _read_stream() -> None:
            """Read stdout until EOF or the output budget is exhausted."""
            nonlocal output_bytes
            assert process.stdout is not None
            while True:
                chunk = await process.stdout.read(_READ_CHUNK)
                if not chunk:
                    break
                decoded = chunk.decode(errors="replace")
                output_chunks.append(decoded)
                output_bytes += len(decoded)
                if output_bytes >= MAX_OUTPUT_CHARS:
                    # Budget exceeded — kill the process immediately so it
                    # stops producing output and consuming CPU/disk.
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

            # Always disarm the cancel-waiter to prevent a dangling task from
            # keeping the event loop alive after this coroutine returns.
            if not cancel_waiter_task.done():
                cancel_waiter_task.cancel()
                try:
                    await cancel_waiter_task
                except (asyncio.CancelledError, Exception):
                    pass

            if not done:
                # Timeout: kill the process tree and drain the read task.
                timed_out = True
                _kill_process_tree(process)
                read_task.cancel()
                try:
                    await read_task
                except (asyncio.CancelledError, Exception):
                    pass
            elif cancel_event.is_set() and not read_task.done():
                # Explicit cancellation by the caller.
                _kill_process_tree(process)
                read_task.cancel()
                try:
                    await read_task
                except (asyncio.CancelledError, Exception):
                    pass
            else:
                # Normal completion or budget-exceeded (process already killed
                # inside _read_stream).  Await the read task to propagate any
                # unexpected exceptions.
                try:
                    await read_task
                except (asyncio.CancelledError, Exception):
                    pass

        except Exception:
            _kill_process_tree(process)
            read_task.cancel()
            if not cancel_waiter_task.done():
                cancel_waiter_task.cancel()

        # Wait for the process to fully exit so returncode is populated.
        try:
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except (asyncio.TimeoutError, Exception):
            _kill_process_tree(process)

        if context.cancelled and not timed_out:
            return ToolResult(success=False, output="", error="Cancelled during execution.")

        output = "".join(output_chunks)
        # output_bytes may overshoot by up to one chunk (64 KiB) because we
        # check the budget after appending.  Trim to the hard limit here.
        truncated = len(output) >= MAX_OUTPUT_CHARS
        if truncated:
            output = output[:MAX_OUTPUT_CHARS]

        exit_code = process.returncode if process.returncode is not None else -1
        header = f"$ {command}\n{'─' * 60}\n"
        if truncated:
            tail = output.rfind("\n", MAX_OUTPUT_CHARS - 2000, MAX_OUTPUT_CHARS)
            if tail > 0:
                output = output[:tail]
            footer = (
                f"\n\n[Output truncated at {MAX_OUTPUT_CHARS:,} characters. "
                "Use a more specific command (e.g. limit with head/tail, "
                "filter with grep, or narrow the scope) to see the rest.]"
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
