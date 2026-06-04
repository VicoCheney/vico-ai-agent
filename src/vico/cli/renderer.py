"""
Terminal Renderer
"""

from __future__ import annotations

import asyncio
import re
import sys
import time
import unicodedata
from collections.abc import Callable

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.text import Text

from vico.core.context_manager import ContextStats
from vico.core.types import ToolCall, ToolResult

console = Console(highlight=False)

# ─── ANSI palette ─────────────────────────────────────────────────────────────
_RESET = "\033[0m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_ITALIC = "\033[3m"
_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_CYAN = "\033[36m"
_CYAN_BOLD = "\033[1;36m"
_WHITE_BOLD = "\033[1;37m"
_BRIGHT_BLK = "\033[90m"
_UNDERLINE = "\033[4m"
_CLEAR_LINE = "\033[2K"

_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

# ─── Column widths ─────────────────────────────────────────────────────────────
# Layout (proportions of terminal width, icon+space = 2 fixed cols):
#
#   icon+space : 2     fixed  ("✓ " or "⠋ ")
#   tool_col   : 10%   left-aligned   (min 8)
#   gap        : 2     fixed
#   param_cols : 60%   left-aligned   (min 20, truncated + padded to exact width)
#   gap        : 2     fixed
#   stat_col   : 15%   right-aligned  (min 12)
#
# Total guaranteed ≤ terminal width.


def _col_widths() -> tuple[int, int, int]:
    """Return (tool_col, param_cols, stat_col) based on current terminal width.

    Proportions are fixed at 10 / 60 / 15 percent of the terminal width with
    sensible minimums.  The three columns plus the two 2-char gaps and the
    2-char icon prefix always fit within the terminal.
    """
    import shutil

    term_w = shutil.get_terminal_size(fallback=(100, 24)).columns
    tool_col = max(8, int(term_w * 0.05))
    stat_col = max(15, int(term_w * 0.15))
    param_cols = max(48, int(term_w * 0.60))
    return tool_col, param_cols, stat_col


def _write(s: str) -> None:
    sys.stdout.write(s)
    sys.stdout.flush()


def _is_tty() -> bool:
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def _char_width(ch: str) -> int:
    """Return the terminal display width of a single character.

    'W' (Wide) and 'F' (Full-width) are always 2 columns.
    'A' (Ambiguous) characters such as box-drawing glyphs (═ ─ │) render as
    2 columns in most modern terminals (macOS Terminal, iTerm2, VS Code) so we
    treat them as 2 as well.  Narrow / neutral / half-width chars are 1 column.

    Exceptions to the Ambiguous=2 rule:
    - U+2026 '…' (HORIZONTAL ELLIPSIS): eaw=A but renders as 1 column in all
      common terminals; treating it as 2 would cause pad_to_width to skip the
      final space when a truncated string ends with '…', misaligning the stat
      column by one position.

    Variation selectors (U+FE00–U+FE0F, U+E0100–U+E01EF) and combining marks
    have zero display width — they modify the preceding character.
    """
    cp = ord(ch)
    # Variation selectors: zero-width modifiers that change glyph presentation
    if 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF:
        return 0
    # Combining diacritical marks and other zero-width combining characters
    cat = unicodedata.category(ch)
    if cat in ("Mn", "Me", "Cf"):
        return 0
    # U+2026 HORIZONTAL ELLIPSIS: eaw=A but universally renders as 1 column
    if cp == 0x2026:
        return 1
    eaw = unicodedata.east_asian_width(ch)
    return 2 if eaw in ("W", "F", "A") else 1


def _wcslen(s: str) -> int:
    """Terminal display width of a plain (no ANSI) string."""
    return sum(_char_width(ch) for ch in s)


def _truncate_by_width(s: str, max_width: int) -> str:
    """Truncate s so its display width ≤ max_width, appending '…' if truncated."""
    w = 0
    for i, ch in enumerate(s):
        cw = _char_width(ch)
        if cw == 0:
            # Zero-width character: always fits, doesn't advance column
            continue
        if w + cw > max_width - 1:
            return s[:i] + "…"
        w += cw
    return s


def _pad_to_width(s: str, target_cols: int) -> str:
    """Pad s with spaces so its display width equals target_cols."""
    current = _wcslen(s)
    if current >= target_cols:
        return s
    return s + " " * (target_cols - current)


# ─── Rich style constants ──────────────────────────────────────────────────────
PRIMARY = "cyan"
SECONDARY = "bright_black"
SUCCESS = "green"
ERROR = "red"
WARNING = "yellow"
SEPARATOR_COLOR = "bright_black"
AGENT_NAME_STYLE = "cyan bold"
TOOL_NAME_STYLE = "magenta bold"


# ─── Permission box helpers ──────────────────────────────────────────────────

# _PERM_BOX_WIDTH removed (dead code)


def _fmt_perm_param(tool_call: ToolCall, cwd: str = "") -> str:
    """Return the most informative single-line parameter string for this tool call."""
    if not tool_call.input:
        return ""
    for key in ("command", "path", "query", "pattern", "url", "file"):
        if key in tool_call.input:
            val = str(tool_call.input[key])
            if key in ("path", "file") and cwd and val.startswith(cwd):
                val = val[len(cwd) :].lstrip("/\\")
            # Collapse multi-line values (e.g. shell scripts) to a single line
            val = " ".join(line.strip() for line in val.splitlines() if line.strip())
            return val
    first_key = next(iter(tool_call.input))
    val = str(tool_call.input[first_key])
    val = " ".join(line.strip() for line in val.splitlines() if line.strip())
    return val


def _visible_width(s: str) -> int:
    """Terminal display width of a string that may contain ANSI escape codes."""
    plain = re.sub(r"\033\[[0-9;]*[mK]", "", s)
    return _wcslen(plain)


def _perm_box_line(content: str, inner_w: int) -> str:
    """Format a single content row inside the permission box, padded to inner_w columns."""
    visible = _visible_width(content)
    pad = max(0, inner_w - visible)
    return f"{_BRIGHT_BLK}│{_RESET}  {content}{' ' * pad}  {_BRIGHT_BLK}│{_RESET}"


def _build_permission_box(tool_call: ToolCall, cwd: str = "") -> list[str]:
    """Build the permission card lines (without trailing newlines)."""
    import shutil

    term_w = shutil.get_terminal_size(fallback=(100, 24)).columns
    _PERM_PARAM_MAX_COLS = max(40, int(term_w * 0.50))

    param_str = _fmt_perm_param(tool_call, cwd)

    title_plain = " \U0001f510 Permission Required "
    tool_plain = f"Tool    {tool_call.name}"

    if param_str:
        is_cmd = "command" in tool_call.input
        raw_display = ("$ " + param_str) if is_cmd else param_str
        display_param = _truncate_by_width(raw_display, _PERM_PARAM_MAX_COLS)
    else:
        display_param = ""

    param_plain = f"Run     {display_param}" if display_param else ""

    extra_rows_plain: list[str] = []
    extra_keys = [k for k in tool_call.input if k not in ("command", "path", "query", "pattern", "url", "file")]
    for key in extra_keys[:2]:
        extra_rows_plain.append(f"{key}    {tool_call.input[key]}")

    all_content_plain = [tool_plain] + ([param_plain] if param_plain else []) + extra_rows_plain
    content_max = max(_wcslen(s) for s in all_content_plain) if all_content_plain else 30

    title_min = _wcslen(title_plain) + 1
    inner_w = max(content_max, title_min)

    dashes_right = max(2, inner_w + 3 - _wcslen(title_plain))
    _DASH = "\u2500"
    top = (
        f"{_BRIGHT_BLK}\u256d{_DASH}{_RESET}"
        f"{_BOLD}{_YELLOW}{title_plain}{_RESET}"
        f"{_BRIGHT_BLK}{_DASH * dashes_right}\u256e{_RESET}"
    )
    bottom = f"{_BRIGHT_BLK}\u2570{_DASH * (inner_w + 4)}\u256f{_RESET}"

    tool_colored = f"{_BOLD}{_YELLOW}Tool{_RESET}    {_CYAN_BOLD}{tool_call.name}{_RESET}"
    lines = [top, _perm_box_line(tool_colored, inner_w)]

    if display_param:
        param_colored = f"{_DIM}Run{_RESET}     {_DIM}{display_param}{_RESET}"
        lines.append(_perm_box_line(param_colored, inner_w))

    for plain_row in extra_rows_plain:
        key, _, val = plain_row.partition("    ")
        colored_row = f"{_DIM}{key}{_RESET}    {_DIM}{val}{_RESET}"
        lines.append(_perm_box_line(colored_row, inner_w))

    lines.append(bottom)
    return lines


def _fmt_approval_summary(
    decision: str,
    tool_name: str,
    param: str,
) -> str:
    """Compact single-line summary shown after the user makes a decision.

    Uses the same proportional column widths as _fmt_done / _fmt_running.
    """
    if decision == "approve_always":
        icon, icon_color = "✓", _GREEN
        stat = "approved always"
        stat_color = _GREEN + _BOLD
    elif decision == "approve":
        icon, icon_color = "✓", _GREEN
        stat = "approved"
        stat_color = _GREEN
    else:
        icon, icon_color = "✗", _RED
        stat = "denied"
        stat_color = _BRIGHT_BLK

    _tool_col, _param_cols, _stat_col = _col_widths()
    name_col = _collapse_to_single_line(tool_name).ljust(_tool_col)
    param_col = _pad_to_width(_truncate_by_width(_collapse_to_single_line(param), _param_cols), _param_cols)
    stat_r = stat.rjust(_stat_col)
    return (
        f"{icon_color}{icon}{_RESET}"
        f" {_CYAN_BOLD}{name_col}{_RESET}"
        f"  {_DIM}{param_col}{_RESET}"
        f"  {stat_color}{stat_r}{_RESET}"
    )


# ─── Tool label helpers ────────────────────────────────────────────────────────


def _collapse_to_single_line(s: str) -> str:
    """Collapse any multi-line string to a single space-separated line.

    This is the single authoritative place for that normalisation so that
    every code path (spinner, done-row, approval summary) is guaranteed to
    produce exactly one terminal line for the param column.
    """
    return " ".join(part.strip() for part in s.splitlines() if part.strip())


def _tool_label(tool_call: ToolCall, cwd: str = "") -> tuple[str, str]:
    """Return (tool_name, param_str) where param is always a single line.

    The param is:
    - stripped of the cwd prefix for path/file keys
    - collapsed to one space-separated line (multi-line commands, etc.)
    - NOT truncated here; callers must truncate to their own _param_cols budget
      via _truncate_by_width / _pad_to_width before writing to the terminal.
    """
    if not tool_call.input:
        return tool_call.name, ""

    def _normalise(key: str, raw: str) -> str:
        val = raw
        if key in ("path", "file") and cwd and val.startswith(cwd):
            val = val[len(cwd) :].lstrip("/\\")
        return _collapse_to_single_line(val)

    for key in ("path", "command", "query", "pattern", "url", "file"):
        if key in tool_call.input:
            return tool_call.name, _normalise(key, str(tool_call.input[key]))
    first_key = next(iter(tool_call.input))
    return tool_call.name, _normalise(first_key, str(tool_call.input[first_key]))


def _fmt_stat(result: ToolResult) -> str:
    approval = result.metadata.get("approval", "") if result.metadata else ""
    if approval:
        return approval
    if result.success:
        n = len(result.output.splitlines())
        return f"{n} ln" if n else "ok"
    err = result.error or "failed"
    m = re.search(r"exit(?:ed)?(?: with)?(?: code)?\s*(\d+)", err, re.I)
    return f"exit {m.group(1)}" if m else err[:8]


def _fmt_running(frame: str, name: str, param: str) -> str:
    """Single-line spinner row: <frame> <tool_col>  <param_cols>

    param must already be a single line (ensured by _tool_label); we only
    truncate + pad it here to fit exactly within _param_cols.
    """
    _tool_col, _param_cols, _ = _col_widths()
    name_col = _collapse_to_single_line(name).ljust(_tool_col)
    param_col = _pad_to_width(_truncate_by_width(_collapse_to_single_line(param), _param_cols), _param_cols)
    return f"{_BRIGHT_BLK}{frame} {_RESET}{_CYAN_BOLD}{name_col}{_RESET}  {_DIM}{param_col}{_RESET}"


def _fmt_done(success: bool, name: str, param: str, stat: str) -> str:
    """Single-line done row: <icon> <tool_col>  <param_cols>  <stat_col>

    Enforces the three-column layout regardless of what the caller supplies.
    """
    _tool_col, _param_cols, _stat_col = _col_widths()
    icon_color = _GREEN if success else _RED
    icon = "✓" if success else "✗"
    name_col = _collapse_to_single_line(name).ljust(_tool_col)
    param_col = _pad_to_width(_truncate_by_width(_collapse_to_single_line(param), _param_cols), _param_cols)
    approval_labels = {"approved", "approved always", "auto approved", "denied"}
    if stat in approval_labels:
        if stat == "approved always":
            stat_color = _GREEN + _BOLD
        elif stat in ("approved", "auto approved"):
            stat_color = _GREEN
        else:
            stat_color = _BRIGHT_BLK
        stat_r = stat.rjust(_stat_col)
        return (
            f"{icon_color}{icon}{_RESET}"
            f" {_CYAN_BOLD}{name_col}{_RESET}"
            f"  {_DIM}{param_col}{_RESET}"
            f"  {stat_color}{stat_r}{_RESET}"
        )
    stat_r = stat.rjust(_stat_col)
    return (
        f"{icon_color}{icon}{_RESET}"
        f" {_CYAN_BOLD}{name_col}{_RESET}"
        f"  {_DIM}{param_col}{_RESET}"
        f"  {_BRIGHT_BLK}{stat_r}{_RESET}"
    )


# ─── Stats footer ─────────────────────────────────────────────────────────────


def _fmt_footer(elapsed_s: float, prompt_tokens: int, completion_tokens: int, context_pct: float) -> str:
    if context_pct >= 80:
        pct_col = _RED
    elif context_pct >= 60:
        pct_col = _YELLOW
    else:
        pct_col = _GREEN

    t = f"time {elapsed_s:.1f}s"
    tok_in = f"{prompt_tokens:,}" if prompt_tokens else "\u2014"
    tok_out = f"{completion_tokens:,}" if completion_tokens else "\u2014"
    pct_plain = f"{context_pct:.0f}%"

    inner_plain = (
        f"  {t}  \u00b7  input tokens {tok_in}  \u00b7  output tokens {tok_out}  \u00b7  context usage {pct_plain}  "
    )

    inner_colored = (
        f"  {_BRIGHT_BLK}{t}{_RESET}"
        f"  {_DIM}\u00b7{_RESET}  input tokens {_BRIGHT_BLK}{tok_in}{_RESET}"
        f"  {_DIM}\u00b7{_RESET}  output tokens {_BRIGHT_BLK}{tok_out}{_RESET}"
        f"  {_DIM}\u00b7{_RESET}  context usage {pct_col}{pct_plain}{_RESET}  "
    )

    # ═ renders as 1 column on macOS Terminal.
    inner_cols = _wcslen(inner_plain)
    border = "\u2550" * inner_cols
    return (
        f"{_BRIGHT_BLK}\u2554{border}\u2557{_RESET}\n"
        f"{_BRIGHT_BLK}\u2551{_RESET}{inner_colored}{_BRIGHT_BLK}\u2551{_RESET}\n"
        f"{_BRIGHT_BLK}\u255a{border}\u255d{_RESET}"
    )


# ─── Renderer ─────────────────────────────────────────────────────────────────


class TerminalRenderer:
    """
    Purely presentational.
    TTY: spinner + in-place overwrites for tools; rich.Live for text.
    Non-TTY: plain sequential output.
    """

    def __init__(self) -> None:
        self._model_label: str = ""
        self._cwd: str = ""

        # per-turn state
        self._agent_label_printed = False
        self._had_tool_output = False
        self._thinking_active = False
        self._thinking_buf: list[str] = []

        # timing + token tracking
        self._turn_start_time: float = 0.0
        self._last_prompt_tokens: int = 0
        self._last_completion_tokens: int = 0

        # text streaming: buffer all chunks, render once via rich.Markdown at end
        self._text_buf: list[str] = []
        self._streaming_text: bool = False
        self._live: Live | None = None

        # batch tool tracking  id → (index, name, param)
        self._batch: dict[str, tuple[int, str, str]] = {}
        self._batch_size = 0
        self._done_ids: set[str] = set()

        self._render_lock: asyncio.Lock = asyncio.Lock()
        self._spinner_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._frame_idx = 0
        self._perm_box_line_count: int = 0
        self._perm_box_tool_name: str = ""
        self._perm_box_param: str = ""

        # Optional callback: (ToolCall) -> bool — True means auto-approved (no dialog)
        # Set by the caller via set_permissions_checker().
        self._permissions_checker: Callable[[ToolCall], bool] | None = None

    def set_model_label(self, provider: str, model: str) -> None:
        self._model_label = f"({provider}/{model})"

    def set_cwd(self, cwd: str) -> None:
        self._cwd = cwd

    def set_permissions_checker(self, checker: Callable[[ToolCall], bool]) -> None:
        """Register a callback that returns True if a tool call is auto-approved.

        When registered, on_tool_call() will suppress the spinner line for tools
        that need a permission dialog (so the dialog can take over the terminal).
        """
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
        console.print(Text("─" * 60, style=SEPARATOR_COLOR))

    # ── Per-turn reset ──────────────────────────────────────────────────────

    def reset_output_state(self) -> None:
        self._agent_label_printed = False
        self._had_tool_output = False
        self._thinking_active = False
        self._thinking_buf.clear()
        self._text_buf.clear()
        self._streaming_text = False
        self._live = None
        self._batch.clear()
        self._done_ids.clear()
        self._batch_size = 0
        self._turn_start_time = time.monotonic()
        self._last_prompt_tokens = 0
        self._last_completion_tokens = 0

    # ── Agent callbacks ─────────────────────────────────────────────────────

    def on_thinking(self, content: str) -> None:
        """Accumulate thinking text; show compact summary when done."""
        self._ensure_agent_label()
        self._stop_spinner()
        self._stop_live()
        if not self._thinking_active:
            self._thinking_active = True
            if self._had_tool_output:
                _write("\n")
                self._had_tool_output = False
            _write(f"{_DIM}💭 Thinking...{_RESET}\n")
        self._thinking_buf.append(content)

    def on_text(self, content: str) -> None:
        self._ensure_agent_label()
        if content:
            self._end_thinking_compact()

        if self._had_tool_output and not self._text_buf:
            _write("\n")
            self._had_tool_output = False

        self._text_buf.append(content)

        if content:
            self._streaming_text = True

    def on_tool_call(self, tool_call: ToolCall) -> None:
        self._ensure_agent_label()
        self._stop_live(finalize=True)
        self._end_thinking_compact()

        name, param = _tool_label(tool_call, cwd=self._cwd)
        idx = self._batch_size
        self._batch[tool_call.id] = (idx, name, param)
        self._batch_size += 1

        # For tools that will need a permission approval dialog, do NOT
        # write a spinner line here: print_permission_request() takes over
        # the terminal.  We still register the tool in self._batch above so
        # that on_tool_result() can write the final summary line correctly.
        needs_approval = not self._permissions_checker(tool_call) if self._permissions_checker else False
        if not needs_approval:
            frame = _FRAMES[self._frame_idx % len(_FRAMES)]
            _write(_fmt_running(frame, name, param) + "\n")
            self._had_tool_output = True

            if _is_tty() and self._spinner_task is None:
                try:
                    loop = asyncio.get_running_loop()
                    self._spinner_task = loop.create_task(self._spin_loop())
                except RuntimeError:
                    pass

    def on_tool_result(self, tool_call: ToolCall, result: ToolResult) -> None:
        stat = _fmt_stat(result)
        if tool_call.id not in self._batch:
            # Tool was never registered (should not happen with current flow).
            return

        idx, name, param = self._batch[tool_call.id]
        self._done_ids.add(tool_call.id)

        # Check if this tool went through the permission dialog.
        # If so, collapse_permission_request() already wrote the approval summary
        # line in the correct terminal position.  Writing another done_line via
        # _overwrite_async would produce a duplicate line (the second stray ✓ row
        # seen in the case).  Skip the overwrite for approved/denied tools.
        was_approved = bool(
            result.metadata and result.metadata.get("approval") in ("approved", "approved always", "denied")
        )
        if was_approved:
            self._had_tool_output = True
            return

        # Auto-approved path: overwrite the spinner row in-place.
        done_line = _fmt_done(result.success, name, param, stat)
        if _is_tty():
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._overwrite_async(idx, done_line))
            except RuntimeError:
                _write(done_line + "\n")
        else:
            _write(done_line + "\n")
        self._had_tool_output = True

    # ── Token tracking ──────────────────────────────────────────────────────

    def on_done_with_usage(self, prompt_tokens: int, completion_tokens: int) -> None:
        self._last_prompt_tokens = prompt_tokens
        self._last_completion_tokens = completion_tokens

    # ── Spinner ─────────────────────────────────────────────────────────────

    async def _spin_loop(self) -> None:
        """Animate the spinner for all pending tool rows.

        Strategy: build one contiguous ANSI string that
          1. jumps to the topmost pending row
          2. rewrites every pending row top-to-bottom
          3. moves the cursor back to the bottom (after the last tool row)
        This keeps all cursor movement inside a single write() call so
        that concurrent on_tool_call() / _overwrite_async() calls that
        hold the same render_lock never interfere with mid-flight cursor
        positioning.
        """
        try:
            while True:
                await asyncio.sleep(0.08)
                self._frame_idx += 1
                frame = _FRAMES[self._frame_idx % len(_FRAMES)]
                async with self._render_lock:
                    pending = sorted(
                        [(tid, info) for tid, info in self._batch.items() if tid not in self._done_ids],
                        key=lambda x: x[1][0],  # sort by idx (top row first)
                    )
                    if not pending:
                        break

                    # Cursor is currently sitting one line below the last
                    # tool row (the \n after each _write call leaves it there).
                    # _batch_size == total rows written; cursor is at row index
                    # _batch_size (0-indexed from first tool row).
                    #
                    # We jump to the topmost pending row, then write each row
                    # sequentially.  At the end the cursor is just below the
                    # last rewritten row; we move it down to row _batch_size.

                    topmost_idx = pending[0][1][0]
                    # lines to move up from current position (below last row)
                    # current cursor row index = _batch_size
                    # target row index = topmost_idx
                    # rows to move up = _batch_size - topmost_idx
                    lines_to_top = self._batch_size - topmost_idx
                    buf = f"\033[{lines_to_top}A"  # move up to topmost row

                    prev_idx = topmost_idx
                    for _, (idx, name, param) in pending:
                        # skip blank rows between pending rows
                        gap = idx - prev_idx
                        if gap > 0:
                            buf += f"\033[{gap}B"  # move down gap rows
                        buf += f"\r{_CLEAR_LINE}{_fmt_running(frame, name, param)}"
                        prev_idx = idx

                    # Move cursor back down to _batch_size row
                    # (one below the last tool row)
                    rows_to_bottom = self._batch_size - prev_idx
                    if rows_to_bottom > 0:
                        buf += f"\033[{rows_to_bottom}B"
                    buf += "\r"

                    sys.stdout.write(buf)
                    sys.stdout.flush()
        finally:
            self._spinner_task = None

    async def _overwrite_async(self, idx: int, new_line: str) -> None:
        """Overwrite a specific tool row in-place (used when tool completes)."""
        async with self._render_lock:
            lines_up = self._batch_size - idx
            sys.stdout.write(f"\033[{lines_up}A\r{_CLEAR_LINE}{new_line}\n\033[{lines_up - 1}B")
            sys.stdout.flush()

    def _stop_spinner(self) -> None:
        if self._spinner_task and not self._spinner_task.done():
            self._spinner_task.cancel()
            self._spinner_task = None

    # ── rich.Live text rendering ─────────────────────────────────────────────

    def _refresh_live(self) -> None:
        """No-op: kept for API compatibility."""

    def _stop_live(self, finalize: bool = False) -> None:
        """Finalize a text block: render buffered content via rich.Markdown."""
        if self._live is not None:
            self._live.stop()
            self._live = None

        if finalize and self._text_buf:
            full = "".join(self._text_buf)
            console.print(Markdown(full))

        if finalize:
            self._text_buf.clear()
            self._streaming_text = False

    # ── Lifecycle callbacks ──────────────────────────────────────────────────

    def on_error(self, error: Exception) -> None:
        self._stop_spinner()
        self._stop_live(finalize=False)
        self._end_thinking_compact()
        console.print()
        console.print(Text(f"  ✗  {error}", style=f"bold {ERROR}"))

    def on_done(self) -> None:
        pass

    def on_loop(self, iteration: int) -> None:
        self._stop_spinner()
        self._stop_live(finalize=True)
        self._batch.clear()
        self._done_ids.clear()
        self._batch_size = 0

    def flush(self) -> None:
        self._stop_spinner()
        self._stop_live(finalize=True)
        self._end_thinking_compact()
        self._batch.clear()
        self._done_ids.clear()
        self._batch_size = 0

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _ensure_agent_label(self) -> None:
        if not self._agent_label_printed:
            console.print()
            label = f"🤖 Vico{self._model_label}:"
            console.print(Text(label, style=AGENT_NAME_STYLE))
            console.print()
            self._agent_label_printed = True

    def _end_thinking_compact(self) -> None:
        """Show a one-line thinking summary capped at 75% of terminal width."""
        if not self._thinking_active:
            return
        self._thinking_active = False

        full = "".join(self._thinking_buf).replace("\n", " ").strip()
        self._thinking_buf.clear()
        if full:
            import shutil

            term_w = shutil.get_terminal_size(fallback=(100, 24)).columns
            max_w = max(40, int(term_w * 0.75))
            summary = _truncate_by_width(full, max_w)
            _write(f"{_DIM}{_ITALIC}{summary}{_RESET}\n\n")

    # ── Permission prompt ────────────────────────────────────────────────────

    def print_permission_request(self, tool_call: ToolCall) -> None:
        """Render the permission card and record its geometry for collapse."""
        self._stop_spinner()
        self._stop_live(finalize=True)
        self._end_thinking_compact()

        lines = _build_permission_box(tool_call, self._cwd)
        _write("\n")
        for ln in lines:
            _write(ln + "\n")
        sys.stdout.flush()

        self._perm_box_line_count = 1 + len(lines)
        self._perm_box_tool_name = tool_call.name
        self._perm_box_param = _fmt_perm_param(tool_call, self._cwd)
        self._had_tool_output = True

    def collapse_permission_request(self, decision: str) -> None:
        """Replace the permission card with a single compact summary line.

        Moves up _perm_box_line_count lines, erases to end-of-screen, then
        writes the one-line summary.  Updates _batch_size so that any
        remaining spinner-row offsets stay valid.
        """
        summary = _fmt_approval_summary(decision, self._perm_box_tool_name, self._perm_box_param)
        if _is_tty() and self._perm_box_line_count > 0:
            n = self._perm_box_line_count
            sys.stdout.write(f"\033[{n}A\r\033[J{summary}\n")
            sys.stdout.flush()
            # The permission box occupied n lines; it's now replaced by 1 summary
            # line.  Increase _batch_size by (n - 1) so that overwrite offsets for
            # any OTHER spinner rows that were written BEFORE the permission box
            # remain correct.  The current tool's own "row" is represented by the
            # summary line itself — its idx in self._batch was set in on_tool_call()
            # and points to the logical position above the current cursor.
            self._batch_size += n - 1
        else:
            _write(summary + "\n")
        self._perm_box_line_count = 0

    # ── Status helpers ───────────────────────────────────────────────────────

    def print_error(self, error: Exception) -> None:
        self._stop_live(finalize=False)
        self._end_thinking_compact()
        console.print()
        console.print(Text(f"  ✗  {error}", style=f"bold {ERROR}"))

    def print_aborted(self) -> None:
        self._stop_spinner()
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
            + _fmt_footer(
                elapsed_s=elapsed,
                prompt_tokens=self._last_prompt_tokens,
                completion_tokens=self._last_completion_tokens,
                context_pct=stats.usage_percent,
            )
            + "\n"
        )
