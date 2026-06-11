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
    collapse_to_single_line,  # noqa: F401 — re-used by callers
    is_tty,
    strip_internal_tags,
    truncate_by_width,
    write,
)
from vico.cli.spinner import SpinnerController, ToolRowController
from vico.cli.tool_format import (
    fmt_done,
    fmt_footer,
    fmt_running,
    fmt_stat,
    tool_label,
)
from vico.config.types.config import ContextStats
from vico.tools.types.call import ToolCall
from vico.tools.types.execution import ToolResult
from vico.utils.terminal import terminal_width

console = Console(highlight=False)

# ─── Tiny state dataclass ────────────────────────────────────────────────────


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


# ─── Renderer ─────────────────────────────────────────────────────────────────


class TerminalRenderer:
    """
    Purely presentational — TTY: spinner + in-place overwrites; Non-TTY: plain output.

    Loading states (TTY only):
    1. waiting spinner  – from reset_output_state() until first LLM chunk
    2. thinking spinner – dynamic frame + live thinking preview
    3. generating spinner – while buffering final Markdown for render

    Spinner animation and tool-row management are delegated to:
      SpinnerController   (cli/spinner.py)
      ToolRowController   (cli/spinner.py)
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
        self._tw_tracker: TypewriterTracker | None = None
        self._typewriter_lines: int = 0

        # Shared spinner frame counter (mutable list so controllers share it by ref)
        self._frame_counter: list[int] = [0]

        # Spinner controllers (each groups task + start_time + active flag)
        self._waiting = SpinnerController()
        self._generating = SpinnerController()
        self._thinking_spinner = SpinnerController()
        for sc in (self._waiting, self._generating, self._thinking_spinner):
            sc.attach_frame_counter(self._frame_counter)

        # Tool batch tracking
        self._tools = ToolRowController()
        self._tools.attach_frame_counter(self._frame_counter)

        # Permission card geometry
        self._perm_box = _PermBoxState()

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
        console.print(Text("─" * terminal_width(), style=SEPARATOR_COLOR))

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
        if not is_tty():
            return
        self._waiting.start()
        try:
            loop = asyncio.get_running_loop()
            self._waiting.task = loop.create_task(
                self._waiting.run_phase_spinner([
                    (0.0, "Connecting"),
                    (2.0, "Waiting for response"),
                    (6.0, "Still thinking"),
                    (15.0, "Taking a while"),
                ])
            )
        except RuntimeError:
            self._waiting.active = False

    def _stop_waiting(self) -> None:
        self._waiting.stop_line()

    # ── Agent callbacks ─────────────────────────────────────────────────────

    def on_thinking(self, content: str) -> None:
        self._ensure_agent_label()
        self._tools.stop_spinner()
        self._stop_generating()
        self._stop_live()
        if not self._thinking_active:
            self._thinking_active = True
            self._thinking_spinner.start()
            write(f"\r{_CLEAR_LINE}")
            write("\n")
            if self._had_tool_output:
                self._had_tool_output = False
            write(f"{_DIM}🧠 Thinking (0.0s){_RESET}\n")
            write(f"{_DIM}⠋⠋ …{_RESET}\n")
            if is_tty() and self._thinking_spinner.task is None:
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
            write("\n")
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
            frame = _FRAMES[self._frame_counter[0] % len(_FRAMES)]
            write(fmt_running(frame, name, param) + "\n")
            self._had_tool_output = True

            if is_tty() and self._tools.spinner_task is None:
                try:
                    loop = asyncio.get_running_loop()
                    self._tools.spinner_task = loop.create_task(self._tools.spin_loop(fmt_running))
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
        if is_tty():
            self._tools.stop_spinner()
            self._tools.overwrite_sync(idx, done_line)
        else:
            write(done_line + "\n")
        self._had_tool_output = True

    # ── Generating spinner ───────────────────────────────────────────────────

    def _start_generating(self) -> None:
        if not is_tty():
            return
        self._generating.start()
        try:
            loop = asyncio.get_running_loop()
            self._generating.task = loop.create_task(
                self._generating.run_phase_spinner([
                    (0.0, "Generating response"),
                    (5.0, "Still generating"),
                    (12.0, "Almost there"),
                ])
            )
        except RuntimeError:
            self._generating.active = False

    def _stop_generating(self) -> None:
        self._generating.stop_line()

    # ── Token tracking ──────────────────────────────────────────────────────

    def on_done_with_usage(self, prompt_tokens: int, completion_tokens: int) -> None:
        self._last_prompt_tokens = prompt_tokens
        self._last_completion_tokens = completion_tokens

    # ── Thinking spinner ─────────────────────────────────────────────────────

    async def _thinking_spin_loop(self) -> None:
        """Animate the two-line thinking indicator with snippet and elapsed time."""
        try:
            while True:
                await asyncio.sleep(0.1)
                self._frame_counter[0] += 1
                frame = _FRAMES[self._frame_counter[0] % len(_FRAMES)]
                elapsed = time.monotonic() - self._thinking_spinner.start_time

                raw = "".join(self._thinking_buf).replace("\n", " ").strip()
                tw = terminal_width()
                snippet_budget = max(20, int(tw * 0.80) - 4)
                snippet = truncate_by_width(raw, snippet_budget) if raw else "…"

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
        self._thinking_spinner.cancel_task()

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
            full = strip_internal_tags(full)
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
        self._tools.stop_spinner()
        self._stop_generating()
        self._stop_live(finalize=False)
        self._end_thinking_compact()
        console.print()
        console.print(Text(f"  ✗  {error}", style=f"bold {ERROR}"))

    def on_done(self) -> None:
        pass

    def on_loop(self, iteration: int) -> None:
        self._tools.stop_spinner()
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
        self._tools.stop_spinner()
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
        summary = truncate_by_width(full, snippet_budget) if full else "…"

        buf = (
            f"\033[2A"
            f"\r{_CLEAR_LINE}"
            f"{_DIM}🧠 Thought ({elapsed:.1f}s){_RESET}\n"
            f"{_CLEAR_LINE}"
            f"{_DIM}—> {_ITALIC}{summary}{_RESET}\n"
            f"\n"
        )
        write(buf)

    # ── Permission prompt ────────────────────────────────────────────────────

    def print_permission_request(self, tool_call: ToolCall) -> None:
        """Render the permission card and record its geometry for collapse."""
        self._tools.stop_spinner()
        self._stop_live(finalize=True)
        self._end_thinking_compact()

        lines = build_permission_box(tool_call, self._cwd)
        write("\n")
        for ln in lines:
            write(ln + "\n")
        sys.stdout.flush()

        self._perm_box.line_count = 1 + len(lines)
        self._perm_box.tool_name = tool_call.name
        self._perm_box.param = fmt_perm_param(tool_call, self._cwd)
        self._had_tool_output = True

    def collapse_permission_request(self, decision: str) -> None:
        """Replace the permission card with a compact summary line."""
        summary = fmt_approval_summary(decision, self._perm_box.tool_name, self._perm_box.param)
        if is_tty() and self._perm_box.line_count > 0:
            n = self._perm_box.line_count
            sys.stdout.write(f"\033[{n}A\r\033[J{summary}\n")
            sys.stdout.flush()
            self._tools.size += n - 1
        else:
            write(summary + "\n")
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
        self._tools.stop_spinner()
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
        write(
            "\n"
            + fmt_footer(
                elapsed_s=elapsed,
                prompt_tokens=self._last_prompt_tokens,
                completion_tokens=self._last_completion_tokens,
                context_pct=stats.usage_percent,
            )
            + "\n"
        )
