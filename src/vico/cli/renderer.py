"""Terminal Renderer — streaming text, spinner animations, tool rows, and stats footer."""

from __future__ import annotations

import asyncio
import shutil
import sys
import time
from collections.abc import Callable

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.text import Text

from vico.cli import theme
from vico.cli.permission_box import (
    build_permission_box,
    fmt_approval_summary,
    fmt_perm_param,
)
from vico.cli.render_utils import (
    TypewriterTracker,
    collapse_to_single_line,
    is_tty,
    strip_internal_tags,
    terminal_width,
    truncate_by_width,
    write,
)
from vico.cli.tool_format import (
    fmt_done,
    fmt_footer,
    fmt_running,
    fmt_stat,
    tool_label,
)
from vico.core.types import ContextStats, ToolCall, ToolResult

console = Console(highlight=False)

# ─── State dataclasses ───────────────────────────────────────────────────────


class _SpinnerState:
    """Mutable state for an animated spinner (waiting / generating / thinking)."""

    __slots__ = ("task", "start_time", "active")

    def __init__(self) -> None:
        self.task: asyncio.Task | None = None  # type: ignore[type-arg]
        self.start_time: float = 0.0
        self.active: bool = False

    def reset(self) -> None:
        self.task = None
        self.start_time = 0.0
        self.active = False


class _ToolBatchState:
    """Mutable state for in-flight tool call batch tracking."""

    __slots__ = ("batch", "size", "done_ids", "overwrite_tasks", "spinner_task", "render_lock")

    def __init__(self) -> None:
        self.batch: dict[str, tuple[int, str, str]] = {}
        self.size: int = 0
        self.done_ids: set[str] = set()
        self.overwrite_tasks: list[asyncio.Task] = []  # type: ignore[type-arg]
        self.spinner_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self.render_lock: asyncio.Lock = asyncio.Lock()

    def clear(self) -> None:
        self.batch.clear()
        self.size = 0
        self.done_ids.clear()
        self.overwrite_tasks.clear()
        self.spinner_task = None


class _PermBoxState:
    """Mutable state for the permission card rendered on screen."""

    __slots__ = ("line_count", "tool_name", "param")

    def __init__(self) -> None:
        self.line_count: int = 0
        self.tool_name: str = ""
        self.param: str = ""

    def reset(self) -> None:
        self.line_count = 0
        self.tool_name = ""
        self.param = ""


# ─── Theme aliases ───────────────────────────────────────────────────────────
_RESET = theme.RESET
_DIM = theme.DIM
_BOLD = theme.BOLD
_ITALIC = theme.ITALIC
_GREEN = theme.GREEN
_RED = theme.RED
_YELLOW = theme.YELLOW
_CYAN = theme.CYAN
_CYAN_BOLD = theme.CYAN_BOLD
_WHITE_BOLD = theme.WHITE_BOLD
_BRIGHT_BLK = theme.BRIGHT_BLK
_UNDERLINE = theme.UNDERLINE
_CLEAR_LINE = theme.CLEAR_LINE
_FRAMES = theme.FRAMES

PRIMARY = theme.PRIMARY
SECONDARY = theme.SECONDARY
SUCCESS = theme.SUCCESS
ERROR = theme.ERROR
WARNING = theme.WARNING
SEPARATOR_COLOR = theme.SEPARATOR_COLOR
AGENT_NAME_STYLE = theme.AGENT_NAME_STYLE
TOOL_NAME_STYLE = theme.TOOL_NAME_STYLE

# Private aliases for render_utils
_strip_internal_tags = strip_internal_tags
_collapse_to_single_line = collapse_to_single_line
_terminal_width = terminal_width
_is_tty = is_tty
_write = write
_truncate_by_width = truncate_by_width
_TypewriterTracker = TypewriterTracker


# ─── Renderer ─────────────────────────────────────────────────────────────────


class TerminalRenderer:
    """
    Purely presentational — TTY: spinner + in-place overwrites; Non-TTY: plain output.

    Loading states (TTY only):
    1. waiting spinner  – from reset_output_state() until first LLM chunk
    2. thinking spinner – dynamic frame + live thinking preview
    3. generating spinner – while buffering final Markdown for render
    """

    def __init__(self) -> None:
        self._model_label: str = ""
        self._cwd: str = ""

        # Per-turn state
        self._agent_label_printed: bool = False
        self._had_tool_output: bool = False
        self._thinking_active: bool = False
        self._thinking_buf: list[str] = []

        # Timing + token tracking
        self._turn_start_time: float = 0.0
        self._last_prompt_tokens: int = 0
        self._last_completion_tokens: int = 0

        # Text streaming buffer
        self._text_buf: list[str] = []
        self._streaming_text: bool = False
        self._live: Live | None = None
        self._tw_tracker: _TypewriterTracker | None = None
        self._typewriter_lines: int = 0

        # Spinner state objects (each groups task + start_time + active flag)
        self._waiting = _SpinnerState()
        self._generating = _SpinnerState()
        self._thinking_spinner = _SpinnerState()

        # Tool batch tracking (spinner rows + overwrite tasks)
        self._tools = _ToolBatchState()

        # Permission card geometry
        self._perm_box = _PermBoxState()

        # Shared spinner frame counter
        self._frame_idx: int = 0

        # Optional callback: (ToolCall) -> bool — True means auto-approved
        self._permissions_checker: Callable[[ToolCall], bool] | None = None

    def set_model_label(self, provider: str, model: str) -> None:
        self._model_label = f"({provider}/{model})"

    def set_cwd(self, cwd: str) -> None:
        self._cwd = cwd

    def set_permissions_checker(self, checker: Callable[[ToolCall], bool]) -> None:
        self._permissions_checker = checker

    # ── Session UI ─────────────────────────────────────────────────────────

    def print_welcome(self) -> None:
        console.print()
        for line in [
            "  ██╗   ██╗ ██╗  ██████╗  ██████╗ ",
            "  ██║   ██║ ██║ ██╔════╝ ██╔═══██╗",
            "  ██║   ██║ ██║ ██║      ██║   ██║",
            "  ╚██╗ ██╔╝ ██║ ██║      ██║   ██║",
            "   ╚████╔╝  ██║ ╚██████╗ ╚██████╔╝",
            "    ╚═══╝   ╚═╝  ╚═════╝  ╚═════╝ ",
        ]:
            console.print(Text(line, style=f"bold {PRIMARY}"))
        console.print()
        console.print(Text("  All-powerful AI agent assistant · Armed with imagination", style=SECONDARY))
        console.print()

    def print_divider(self) -> None:
        console.print(Text("─" * _terminal_width(), style=SEPARATOR_COLOR))

    # ── Per-turn reset ──────────────────────────────────────────────────────

    def reset_output_state(self) -> None:
        self._agent_label_printed = False
        self._had_tool_output = False
        self._thinking_active = False
        self._thinking_buf.clear()
        self._text_buf.clear()
        self._streaming_text = False
        self._live = None
        self._tw_tracker = None
        self._typewriter_lines = 0
        self._tools.clear()
        self._turn_start_time = time.monotonic()
        self._last_prompt_tokens = 0
        self._last_completion_tokens = 0
        self._generating.reset()

    # ── Waiting spinner ─────────────────────────────────────────────────────

    def start_waiting(self) -> None:
        """Start the waiting spinner. Only active in TTY mode."""
        if not _is_tty():
            return
        self._waiting.start_time = time.monotonic()
        self._waiting.active = True
        try:
            loop = asyncio.get_running_loop()
            self._waiting.task = loop.create_task(self._waiting_spin_loop())
        except RuntimeError:
            self._waiting.active = False

    def _stop_waiting(self) -> None:
        if self._waiting.task and not self._waiting.task.done():
            self._waiting.task.cancel()
        self._waiting.task = None
        if self._waiting.active:
            sys.stdout.write(f"\r{_CLEAR_LINE}")
            sys.stdout.flush()
            self._waiting.active = False

    async def _animate_spinner(self, state: _SpinnerState, phases: list[tuple[float, str]]) -> None:
        """Phase-progressive spinner animation loop. Runs until cancelled."""
        try:
            while True:
                await asyncio.sleep(0.08)
                self._frame_idx += 1
                frame = _FRAMES[self._frame_idx % len(_FRAMES)]
                elapsed = time.monotonic() - state.start_time
                label = phases[0][1]
                for threshold, phase_label in phases:
                    if elapsed >= threshold:
                        label = phase_label
                line = f"\r{_CLEAR_LINE}{_DIM}{frame} {label}...  {elapsed:.1f}s{_RESET}"
                sys.stdout.write(line)
                sys.stdout.flush()
        except asyncio.CancelledError:
            pass

    async def _waiting_spin_loop(self) -> None:
        await self._animate_spinner(self._waiting, [
            (0.0, "Connecting"),
            (2.0, "Waiting for response"),
            (6.0, "Still thinking"),
            (15.0, "Taking a while"),
        ])

    # ── Agent callbacks ─────────────────────────────────────────────────────

    def on_thinking(self, content: str) -> None:
        self._ensure_agent_label()
        self._stop_spinner()
        self._stop_generating()
        self._stop_live()
        if not self._thinking_active:
            self._thinking_active = True
            self._thinking_spinner.start_time = time.monotonic()
            _write(f"\r{_CLEAR_LINE}")
            _write("\n")
            if self._had_tool_output:
                self._had_tool_output = False
            _write(f"{_DIM}🧠 Thinking (0.0s){_RESET}\n")
            _write(f"{_DIM}⠋⠋ …{_RESET}\n")
            if _is_tty() and self._thinking_spinner.task is None:
                try:
                    loop = asyncio.get_running_loop()
                    self._thinking_spinner.task = loop.create_task(self._thinking_spin_loop())
                except RuntimeError:
                    pass
        self._thinking_buf.append(content)

    def on_text(self, content: str) -> None:
        """Buffer text; rendered via rich.Markdown at flush time."""
        self._ensure_agent_label()
        if content:
            self._end_thinking_compact()

        if self._had_tool_output and not self._text_buf:
            _write("\n")
            self._had_tool_output = False

        self._text_buf.append(content)

        if content:
            self._streaming_text = True
            if not self._generating.active:
                self._start_generating()

    def on_tool_call(self, tool_call: ToolCall) -> None:
        self._ensure_agent_label()
        self._stop_generating()
        self._stop_live(finalize=True)
        self._end_thinking_compact()

        name, param = tool_label(tool_call, cwd=self._cwd)
        idx = self._tools.size
        self._tools.batch[tool_call.id] = (idx, name, param)
        self._tools.size += 1

        # Tools needing approval: skip spinner; print_permission_request() takes over.
        needs_approval = not self._permissions_checker(tool_call) if self._permissions_checker else False
        if not needs_approval:
            frame = _FRAMES[self._frame_idx % len(_FRAMES)]
            _write(fmt_running(frame, name, param) + "\n")
            self._had_tool_output = True

            if _is_tty() and self._tools.spinner_task is None:
                try:
                    loop = asyncio.get_running_loop()
                    self._tools.spinner_task = loop.create_task(self._spin_loop())
                except RuntimeError:
                    pass

    def on_tool_result(self, tool_call: ToolCall, result: ToolResult) -> None:
        stat = fmt_stat(result)
        if tool_call.id not in self._tools.batch:
            return

        idx, name, param = self._tools.batch[tool_call.id]
        self._tools.done_ids.add(tool_call.id)

        # Tools through approval dialog: collapse_permission_request() already
        # wrote the summary line; skip overwrite to avoid a duplicate row.
        was_approved = bool(
            result.metadata and result.metadata.get("approval") in ("approved", "approved always", "denied")
        )
        if was_approved:
            self._had_tool_output = True
            return

        # Auto-approved: overwrite spinner row synchronously (single-threaded asyncio).
        done_line = fmt_done(result.success, name, param, stat)
        if _is_tty():
            self._stop_spinner()
            self._overwrite_sync(idx, done_line)
        else:
            _write(done_line + "\n")
        self._had_tool_output = True

    # ── Generating spinner (buffering final Markdown, before rich render) ───

    def _start_generating(self) -> None:
        if not _is_tty():
            return
        self._generating.start_time = time.monotonic()
        self._generating.active = True
        try:
            loop = asyncio.get_running_loop()
            self._generating.task = loop.create_task(self._generating_spin_loop())
        except RuntimeError:
            self._generating.active = False

    def _stop_generating(self) -> None:
        if self._generating.task and not self._generating.task.done():
            self._generating.task.cancel()
        self._generating.task = None
        if self._generating.active:
            sys.stdout.write(f"\r{_CLEAR_LINE}")
            sys.stdout.flush()
            self._generating.active = False

    async def _generating_spin_loop(self) -> None:
        await self._animate_spinner(self._generating, [
            (0.0, "Generating response"),
            (5.0, "Still generating"),
            (12.0, "Almost there"),
        ])

    # ── Token tracking ──────────────────────────────────────────────────────

    def on_done_with_usage(self, prompt_tokens: int, completion_tokens: int) -> None:
        self._last_prompt_tokens = prompt_tokens
        self._last_completion_tokens = completion_tokens

    # ── Tool spinner ─────────────────────────────────────────────────────────

    async def _spin_loop(self) -> None:
        """Animate spinner rows for all pending tool calls.

        Builds one ANSI string: jump to topmost pending row, rewrite each row,
        move cursor back to bottom — all in one write() to avoid interleaving.
        """
        try:
            while True:
                await asyncio.sleep(0.08)
                self._frame_idx += 1
                frame = _FRAMES[self._frame_idx % len(_FRAMES)]
                async with self._tools.render_lock:
                    pending = sorted(
                        [(tid, info) for tid, info in self._tools.batch.items() if tid not in self._tools.done_ids],
                        key=lambda x: x[1][0],  # sort by idx (top row first)
                    )
                    if not pending:
                        break

                    topmost_idx = pending[0][1][0]
                    lines_to_top = self._tools.size - topmost_idx
                    buf = f"\033[{lines_to_top}A"

                    prev_idx = topmost_idx
                    for _, (idx, name, param) in pending:
                        gap = idx - prev_idx
                        if gap > 0:
                            buf += f"\033[{gap}B"
                        buf += f"\r{_CLEAR_LINE}{fmt_running(frame, name, param)}"
                        prev_idx = idx

                    rows_to_bottom = self._tools.size - prev_idx
                    if rows_to_bottom > 0:
                        buf += f"\033[{rows_to_bottom}B"
                    buf += "\r"

                    sys.stdout.write(buf)
                    sys.stdout.flush()
        finally:
            self._tools.spinner_task = None

    def _overwrite_sync(self, idx: int, new_line: str) -> None:
        """Synchronously overwrite a tool row in-place.

        Atomic w.r.t. spinner loop (asyncio is single-threaded).
        Move up (size - idx) lines, write done line, move back down.
        """
        lines_up = self._tools.size - idx
        if lines_up <= 0:
            sys.stdout.write(f"\r{_CLEAR_LINE}{new_line}\n")
        elif lines_up == 1:
            sys.stdout.write(f"\033[1A\r{_CLEAR_LINE}{new_line}\n")
        else:
            sys.stdout.write(f"\033[{lines_up}A\r{_CLEAR_LINE}{new_line}\n\033[{lines_up - 1}B")
        sys.stdout.flush()

    async def _overwrite_async(self, idx: int, new_line: str) -> None:
        """Overwrite a tool row in-place under render_lock (prevents spin_loop races)."""
        async with self._tools.render_lock:
            lines_up = self._tools.size - idx
            if lines_up <= 0:
                sys.stdout.write(f"\r{_CLEAR_LINE}{new_line}")
            else:
                sys.stdout.write(f"\033[{lines_up}A\r{_CLEAR_LINE}{new_line}\n\033[{lines_up - 1}B")
            sys.stdout.flush()

    def _stop_spinner(self) -> None:
        if self._tools.spinner_task and not self._tools.spinner_task.done():
            self._tools.spinner_task.cancel()
            self._tools.spinner_task = None

    # ── Thinking spinner ────────────────────────────────────────────────────

    async def _thinking_spin_loop(self) -> None:
        """Animate the two-line thinking indicator with snippet and elapsed time."""
        try:
            while True:
                await asyncio.sleep(0.1)
                self._frame_idx += 1
                frame = _FRAMES[self._frame_idx % len(_FRAMES)]
                elapsed = time.monotonic() - self._thinking_spinner.start_time

                raw = "".join(self._thinking_buf).replace("\n", " ").strip()
                tw = _terminal_width()
                snippet_budget = max(20, int(tw * 0.80) - 4)
                snippet = _truncate_by_width(raw, snippet_budget) if raw else "…"

                buf = (
                    f"\033[2A"
                    f"\r{_CLEAR_LINE}"
                    f"{_DIM}🧠 Thinking ({elapsed:.1f}s){_RESET}\n"
                    f"{_CLEAR_LINE}"
                    f"{_DIM}{frame}{frame} {_ITALIC}{snippet}{_RESET}\n"
                )
                sys.stdout.write(buf)
                sys.stdout.flush()
        except asyncio.CancelledError:
            pass
        finally:
            self._thinking_spinner.task = None

    def _stop_thinking_spinner(self) -> None:
        """Cancel the thinking spinner task (does NOT erase the line)."""
        if self._thinking_spinner.task and not self._thinking_spinner.task.done():
            self._thinking_spinner.task.cancel()
        self._thinking_spinner.task = None

    # ── rich.Live text rendering ─────────────────────────────────────────────

    def _refresh_live(self) -> None:
        """No-op: kept for API compatibility."""

    def _stop_live(self, finalize: bool = False) -> None:
        """Render buffered text via rich.Markdown if finalize=True."""
        if self._live is not None:
            self._live.stop()
            self._live = None

        if finalize and self._text_buf:
            self._stop_generating()
            full = "".join(self._text_buf)
            full = _strip_internal_tags(full)
            if full.strip():
                console.print(Markdown(full))

        if finalize:
            self._text_buf.clear()
            self._streaming_text = False
            self._tw_tracker = None
            self._typewriter_lines = 0

    # ── Lifecycle callbacks ──────────────────────────────────────────────────

    def on_error(self, error: Exception) -> None:
        self._stop_waiting()
        self._stop_thinking_spinner()
        self._stop_spinner()
        self._stop_generating()
        self._stop_live(finalize=False)
        self._end_thinking_compact()
        console.print()
        console.print(Text(f"  ✗  {error}", style=f"bold {ERROR}"))

    def on_done(self) -> None:
        pass

    def on_loop(self, iteration: int) -> None:
        self._stop_spinner()
        self._stop_generating()
        self._stop_live(finalize=True)
        self._tools.clear()

    async def flush_async(self) -> None:
        """Await in-flight overwrite tasks, then call flush()."""
        if self._tools.overwrite_tasks:
            await asyncio.gather(*self._tools.overwrite_tasks, return_exceptions=True)
            self._tools.overwrite_tasks.clear()
        self.flush()

    def flush(self) -> None:
        self._stop_waiting()
        self._stop_thinking_spinner()
        self._stop_spinner()
        self._stop_generating()
        self._stop_live(finalize=True)
        self._end_thinking_compact()
        self._tools.clear()

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _ensure_agent_label(self) -> None:
        if not self._agent_label_printed:
            self._stop_waiting()
            console.print()
            label = f"🤖 Vico{self._model_label}:"
            console.print(Text(label, style=AGENT_NAME_STYLE))
            console.print()
            self._agent_label_printed = True

    def _end_thinking_compact(self) -> None:
        """Stop the thinking spinner and replace the two-line block with the completed format."""
        if not self._thinking_active:
            return
        self._thinking_active = False
        self._stop_thinking_spinner()

        full = "".join(self._thinking_buf).replace("\n", " ").strip()
        self._thinking_buf.clear()

        elapsed = time.monotonic() - self._thinking_spinner.start_time

        term_w = shutil.get_terminal_size(fallback=(100, 24)).columns
        snippet_budget = max(20, int(term_w * 0.80) - 3)
        summary = _truncate_by_width(full, snippet_budget) if full else "…"

        buf = (
            f"\033[2A"
            f"\r{_CLEAR_LINE}"
            f"{_DIM}🧠 Thought ({elapsed:.1f}s){_RESET}\n"
            f"{_CLEAR_LINE}"
            f"{_DIM}—> {_ITALIC}{summary}{_RESET}\n"
            f"\n"
        )
        _write(buf)

    # ── Permission prompt ────────────────────────────────────────────────────

    def print_permission_request(self, tool_call: ToolCall) -> None:
        """Render the permission card and record its geometry for collapse."""
        self._stop_spinner()
        self._stop_live(finalize=True)
        self._end_thinking_compact()

        lines = build_permission_box(tool_call, self._cwd)
        _write("\n")
        for ln in lines:
            _write(ln + "\n")
        sys.stdout.flush()

        self._perm_box.line_count = 1 + len(lines)
        self._perm_box.tool_name = tool_call.name
        self._perm_box.param = fmt_perm_param(tool_call, self._cwd)
        self._had_tool_output = True

    def collapse_permission_request(self, decision: str) -> None:
        """Replace the permission card with a compact summary line."""
        summary = fmt_approval_summary(decision, self._perm_box.tool_name, self._perm_box.param)
        if _is_tty() and self._perm_box.line_count > 0:
            n = self._perm_box.line_count
            sys.stdout.write(f"\033[{n}A\r\033[J{summary}\n")
            sys.stdout.flush()
            self._tools.size += n - 1
        else:
            _write(summary + "\n")
        self._perm_box.line_count = 0

    # ── Status helpers ───────────────────────────────────────────────────────

    def print_error(self, error: Exception) -> None:
        self._stop_live(finalize=False)
        self._end_thinking_compact()
        console.print()
        console.print(Text(f"  ✗  {error}", style=f"bold {ERROR}"))

    def print_aborted(self) -> None:
        self._stop_waiting()
        self._stop_thinking_spinner()
        self._stop_spinner()
        self._stop_generating()
        self._stop_live(finalize=False)
        self._end_thinking_compact()
        console.print()
        console.print(Text("  Aborted.", style=WARNING))

    def print_goodbye(self) -> None:
        console.print()
        console.print(Text("  Goodbye!", style=SECONDARY))
        console.print()

    def print_context_stats(self, stats: ContextStats) -> None:
        elapsed = time.monotonic() - self._turn_start_time if self._turn_start_time else 0.0
        _write(
            "\n"
            + fmt_footer(
                elapsed_s=elapsed,
                prompt_tokens=self._last_prompt_tokens,
                completion_tokens=self._last_completion_tokens,
                context_pct=stats.usage_percent,
            )
            + "\n"
        )
