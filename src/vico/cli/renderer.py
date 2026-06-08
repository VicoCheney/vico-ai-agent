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


def _strip_internal_tags(s: str) -> str:
    """Remove planner/internal XML-ish tags that should never reach the user."""
    # Paired XML-like scaffold blocks.
    for tag in ("plan_summary", "plan", "thinking", "tool_invocation", "tool_call", "function_call", "invoke"):
        out_pattern = rf"<{tag}\b[^>]*>[\s\S]*?</{tag}>"
        s = re.sub(out_pattern, "", s)
    # Self-closing / never-closed fake-invocation tags on their own line.
    s = re.sub(
        r"<(?:tool_invocation|tool_call|function_call|invoke)\b[^>]*/?>",
        "",
        s,
    )
    # Collapse 3+ consecutive newlines that the removal may have left behind.
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip("\n")


def _terminal_width() -> int:
    """Return current terminal width (columns), default 100."""
    import shutil

    return shutil.get_terminal_size(fallback=(100, 24)).columns


class _TypewriterTracker:
    """Track how many physical terminal rows the typewriter output has occupied.

    Unlike a naive per-chunk line counter, this class maintains running state
    across chunks so that line-wrapping is computed correctly when a single
    logical line is split across many small streaming chunks.

    Models *deferred-wrap* terminals (xterm/iTerm2/Terminal.app): writing
    exactly term_w characters leaves the cursor at the last column WITHOUT
    advancing to the next row.  Only the (term_w+1)ˢᵗ character causes a wrap.

    Usage::
        tracker = _TypewriterTracker(term_w)
        for chunk in stream:
            tracker.feed(chunk)
        # rows above the cursor that belong to the typewriter zone:
        rows = tracker.rows_above
    """

    def __init__(self, term_w: int) -> None:
        self._term_w = max(1, term_w)
        # Column offset of the cursor on the current physical row (0-based).
        self._col: int = 0
        # Number of FULLY COMPLETED rows above the cursor's current row
        # (each \n or wrap event increments this).
        self._completed_rows: int = 0

    def feed(self, text: str) -> None:
        """Ingest a raw text chunk (may contain newlines and ANSI escapes)."""
        if not text:
            return
        # Strip ANSI so we measure only printable column widths.
        plain = re.sub(r"\033\[[0-9;]*[mK]", "", text)
        for ch in plain:
            if ch == "\n":
                self._completed_rows += 1
                self._col = 0
                continue
            if ch == "\r":
                self._col = 0
                continue
            cw = _char_width(ch)
            if cw == 0:
                continue
            # Deferred wrap: only wrap when the NEW char would EXCEED term_w.
            # Writing up to and including column term_w leaves the cursor at
            # the boundary; the next printable char moves to the next row.
            if self._col + cw > self._term_w:
                self._completed_rows += 1
                self._col = cw
            else:
                self._col += cw

    @property
    def rows_above(self) -> int:
        """Rows ABOVE the cursor that contain typewriter content.

        This is the number of lines to send to ``\\033[N A`` (cursor up) in
        order to land on the FIRST row of the typewriter zone.  After moving
        up by this amount and emitting ``\\r``, the cursor is at column 0 of
        the typewriter zone's first row, ready for ``\\033[J`` to erase the
        whole zone before re-rendering with rich.Markdown.
        """
        return self._completed_rows

    @property
    def lines(self) -> int:
        """Total physical rows the typewriter content currently occupies.

        Always at least 1 (cursor sits on a row).  Provided for diagnostics;
        production code should use ``rows_above`` for cursor positioning.
        """
        return self._completed_rows + 1


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

    Loading states (TTY only):
    1. waiting spinner  – from reset_output_state() until first LLM chunk arrives
    2. thinking spinner – dynamic frame + truncated live thinking preview
    3. typewriter text  – raw chars streamed live; re-rendered via Markdown on flush
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

        # text streaming: buffer all chunks; typewriter-print live,
        # then re-render via rich.Markdown at the end.
        self._text_buf: list[str] = []
        self._streaming_text: bool = False
        self._live: Live | None = None
        # Tracker for physical rows occupied by typewriter output.
        # Reset at the start of each text block; used to scroll back
        # for the final rich.Markdown re-render.
        self._tw_tracker: _TypewriterTracker | None = None
        self._typewriter_lines: int = 0  # snapshot taken at finalize time

        # batch tool tracking  id → (index, name, param)
        self._batch: dict[str, tuple[int, str, str]] = {}
        self._batch_size = 0
        self._done_ids: set[str] = set()
        # Tracks in-flight _overwrite_async tasks so flush_async() can await
        # them before scrolling the terminal (fixes last-tool spinner freeze).
        self._overwrite_tasks: list[asyncio.Task] = []  # type: ignore[type-arg]

        self._render_lock: asyncio.Lock = asyncio.Lock()
        self._spinner_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._frame_idx = 0
        self._perm_box_line_count: int = 0
        self._perm_box_tool_name: str = ""
        self._perm_box_param: str = ""

        # ── Waiting spinner (between user submit and first LLM chunk) ──────
        self._waiting_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._waiting_start: float = 0.0
        # True while the waiting spinner is occupying a line (so we know to
        # erase it before printing any other content).
        self._waiting_active: bool = False

        # ── Generating spinner (buffering final response, before rich render) ─
        self._generating_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._generating_start: float = 0.0
        self._generating_active: bool = False

        # ── Thinking spinner (replaces static "💭 Thinking..." line) ────────
        self._thinking_spinner_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._thinking_start: float = 0.0

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
        self._tw_tracker = None
        self._typewriter_lines = 0
        self._batch.clear()
        self._done_ids.clear()
        self._batch_size = 0
        self._overwrite_tasks.clear()
        self._turn_start_time = time.monotonic()
        self._last_prompt_tokens = 0
        self._last_completion_tokens = 0
        self._generating_active = False
        self._generating_task = None

    # ── Waiting spinner ─────────────────────────────────────────────────────

    def start_waiting(self) -> None:
        """Start the 'Waiting for response...' spinner immediately after user submit.

        This covers the latency gap between the user pressing Enter and the LLM
        producing its first token.  Call this right after reset_output_state().
        Only active in TTY mode.
        """
        if not _is_tty():
            return
        self._waiting_start = time.monotonic()
        self._waiting_active = True
        try:
            loop = asyncio.get_running_loop()
            self._waiting_task = loop.create_task(self._waiting_spin_loop())
        except RuntimeError:
            # No running event loop (e.g. sync context or test); skip.
            self._waiting_active = False

    def _stop_waiting(self) -> None:
        """Stop the waiting spinner and erase its line.

        Safe to call multiple times; idempotent after the first call.
        """
        if self._waiting_task and not self._waiting_task.done():
            self._waiting_task.cancel()
        self._waiting_task = None
        if self._waiting_active:
            # Erase the spinner line so subsequent output starts cleanly.
            sys.stdout.write(f"\r{_CLEAR_LINE}")
            sys.stdout.flush()
            self._waiting_active = False

    async def _waiting_spin_loop(self) -> None:
        """Animate the waiting-for-LLM spinner until cancelled."""
        # Phase labels keyed by elapsed seconds threshold
        _phases: list[tuple[float, str]] = [
            (0.0, "Connecting"),
            (2.0, "Waiting for response"),
            (6.0, "Still thinking"),
            (15.0, "Taking a while"),
        ]
        try:
            while True:
                await asyncio.sleep(0.08)
                self._frame_idx += 1
                frame = _FRAMES[self._frame_idx % len(_FRAMES)]
                elapsed = time.monotonic() - self._waiting_start
                label = _phases[0][1]
                for threshold, phase_label in _phases:
                    if elapsed >= threshold:
                        label = phase_label
                line = f"\r{_CLEAR_LINE}{_DIM}{frame} {label}...  {elapsed:.1f}s{_RESET}"
                sys.stdout.write(line)
                sys.stdout.flush()
        except asyncio.CancelledError:
            pass

    # ── Agent callbacks ─────────────────────────────────────────────────────

    def on_thinking(self, content: str) -> None:
        """Accumulate thinking text; show animated spinner + live preview while thinking."""
        self._ensure_agent_label()
        self._stop_spinner()
        # Stop the generating spinner first so it doesn't bleed across into the
        # thinking block (otherwise the dim "⠹ Generating response..." line
        # stays on screen above the thinking header).
        self._stop_generating()
        self._stop_live()
        if not self._thinking_active:
            self._thinking_active = True
            self._thinking_start = time.monotonic()
            # Always print a blank line before the thinking block
            # (clear any previous content on the current line first)
            _write(f"\r{_CLEAR_LINE}")
            _write("\n")
            if self._had_tool_output:
                self._had_tool_output = False
            # Print the two-line thinking header that the spinner will overwrite:
            # Line 1: "🧠 Thingking (0.0s)"
            # Line 2: "⠋⠋ <snippet>…"
            _write(f"{_DIM}🧠 Thinking (0.0s){_RESET}\n")
            _write(f"{_DIM}⠋⠋ …{_RESET}\n")
            # Start the thinking spinner task (animates the two lines above in-place)
            if _is_tty() and self._thinking_spinner_task is None:
                try:
                    loop = asyncio.get_running_loop()
                    self._thinking_spinner_task = loop.create_task(self._thinking_spin_loop())
                except RuntimeError:
                    pass
        self._thinking_buf.append(content)

    def on_text(self, content: str) -> None:
        """Buffer text content; rendered once via rich.Markdown at flush time.

        We deliberately do NOT typewriter-stream text chunks to the terminal
        because doing so requires a precise cursor scroll-back at finalize time
        (\033[N A \033[J) to overwrite the raw text with formatted Markdown.
        That cursor math races with concurrent _overwrite_async() (tool result
        row updates) and depends on terminal-specific deferred-wrap behaviour
        — both of which have caused content to be erased.  The waiting and
        thinking spinners already cover the user-perceived latency before the
        first text token; flushing the entire response at once is reliable and
        produces correct Markdown formatting every time.
        """
        self._ensure_agent_label()
        if content:
            self._end_thinking_compact()

        if self._had_tool_output and not self._text_buf:
            _write("\n")
            self._had_tool_output = False

        self._text_buf.append(content)

        if content:
            self._streaming_text = True
            # Start generating spinner on the very first real text chunk:
            # the response is fully buffered and won't be rendered until
            # flush_async(); this spinner bridges that silent wait.
            if not self._generating_active:
                self._start_generating()

    def on_tool_call(self, tool_call: ToolCall) -> None:
        self._ensure_agent_label()
        # Stop the generating spinner BEFORE flushing any buffered text so the
        # spinner line is erased atomically and the Markdown render starts on
        # a clean column-0 line.  Otherwise the spinner frame collides with
        # the final Markdown text on the same line (seen as
        # "⠹ Almost there...  121.0s<plan_summary>..." in case logs).
        self._stop_generating()
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
        # IMPORTANT: do this SYNCHRONOUSLY rather than scheduling an
        # _overwrite_async task.  Python asyncio is single-threaded, so a
        # sync stdout write here is atomic w.r.t. the spin loop (which can
        # only run between awaits).  Deferring the overwrite via create_task
        # used to race with the very next sync callback (on_thinking /
        # on_tool_call) that wrote NEW content to the terminal before the
        # overwrite task got its chance to run — leaving the last tool row
        # frozen on its last spinner frame (e.g. "⠴ bash  system_profiler ...").
        done_line = _fmt_done(result.success, name, param, stat)
        if _is_tty():
            # Stop the spin loop FIRST so it cannot redraw a stale spinner
            # frame over our just-written done line.  It will be restarted
            # by the next on_tool_call() if more tools follow.
            self._stop_spinner()
            self._overwrite_sync(idx, done_line)
        else:
            _write(done_line + "\n")
        self._had_tool_output = True

    # ── Generating spinner (buffering final Markdown, before rich render) ───

    def _start_generating(self) -> None:
        """Start the 'Generating response...' spinner shown while buffering text.

        The spinner occupies a single line and is erased atomically by
        _stop_generating() before the rich.Markdown block is rendered.
        Only active in TTY mode.
        """
        if not _is_tty():
            return
        self._generating_start = time.monotonic()
        self._generating_active = True
        try:
            loop = asyncio.get_running_loop()
            self._generating_task = loop.create_task(self._generating_spin_loop())
        except RuntimeError:
            self._generating_active = False

    def _stop_generating(self) -> None:
        """Stop the generating spinner and erase its line.

        Safe to call multiple times; idempotent after the first call.
        """
        if self._generating_task and not self._generating_task.done():
            self._generating_task.cancel()
        self._generating_task = None
        if self._generating_active:
            sys.stdout.write(f"\r{_CLEAR_LINE}")
            sys.stdout.flush()
            self._generating_active = False

    async def _generating_spin_loop(self) -> None:
        """Animate the generating-response spinner until cancelled."""
        _phases: list[tuple[float, str]] = [
            (0.0, "Generating response"),
            (5.0, "Still generating"),
            (12.0, "Almost there"),
        ]
        try:
            while True:
                await asyncio.sleep(0.08)
                self._frame_idx += 1
                frame = _FRAMES[self._frame_idx % len(_FRAMES)]
                elapsed = time.monotonic() - self._generating_start
                label = _phases[0][1]
                for threshold, phase_label in _phases:
                    if elapsed >= threshold:
                        label = phase_label
                line = f"\r{_CLEAR_LINE}{_DIM}{frame} {label}...  {elapsed:.1f}s{_RESET}"
                sys.stdout.write(line)
                sys.stdout.flush()
        except asyncio.CancelledError:
            pass

    # ── Token tracking ──────────────────────────────────────────────────────

    def on_done_with_usage(self, prompt_tokens: int, completion_tokens: int) -> None:
        self._last_prompt_tokens = prompt_tokens
        self._last_completion_tokens = completion_tokens

    # ── Tool spinner ─────────────────────────────────────────────────────────

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

    def _overwrite_sync(self, idx: int, new_line: str) -> None:
        """Synchronously overwrite a specific tool row in-place.

        Safe because Python's asyncio is single-threaded: between the moment
        this function starts writing and the moment it returns, no other
        coroutine (including the spinner loop) can interleave its own writes.

        Cursor arithmetic mirrors the former _overwrite_async:
          - cursor sits one line below the last tool row (logical row _batch_size)
          - move up (_batch_size - idx) lines to reach row `idx`
          - write the done line followed by \n (advances cursor 1 row)
          - move down (_batch_size - idx - 1) lines back to the bottom
        """
        lines_up = self._batch_size - idx
        if lines_up <= 0:
            sys.stdout.write(f"\r{_CLEAR_LINE}{new_line}\n")
        elif lines_up == 1:
            # idx is the immediately-previous row; just go up one and rewrite,
            # then \n brings cursor back to the bottom — no extra down move.
            sys.stdout.write(f"\033[1A\r{_CLEAR_LINE}{new_line}\n")
        else:
            sys.stdout.write(f"\033[{lines_up}A\r{_CLEAR_LINE}{new_line}\n\033[{lines_up - 1}B")
        sys.stdout.flush()

    async def _overwrite_async(self, idx: int, new_line: str) -> None:
        """Overwrite a specific tool row in-place (used when tool completes).

        Acquires the render lock so that the concurrent _spin_loop never
        writes a stale spinner frame AFTER we have placed the final done line.
        Cursor arithmetic:
          - cursor is one line below the last tool row  (at logical row _batch_size)
          - to reach row `idx` we move up (_batch_size - idx) lines
          - after writing new_line we move back down (_batch_size - idx - 1) lines
            (one less because \n already advanced us one row)
        """
        async with self._render_lock:
            lines_up = self._batch_size - idx
            if lines_up <= 0:
                # idx is already the last row; just overwrite in-place
                sys.stdout.write(f"\r{_CLEAR_LINE}{new_line}")
            else:
                sys.stdout.write(f"\033[{lines_up}A\r{_CLEAR_LINE}{new_line}\n\033[{lines_up - 1}B")
            sys.stdout.flush()

    def _stop_spinner(self) -> None:
        if self._spinner_task and not self._spinner_task.done():
            self._spinner_task.cancel()
            self._spinner_task = None

    # ── Thinking spinner ────────────────────────────────────────────────────

    async def _thinking_spin_loop(self) -> None:
        """Animate the two-line thinking indicator with live snippet and elapsed time.

        Format:
          Line 1: 🧠 Thingking (3.5s)
          Line 2: ⠋⠋ <snippet truncated to 80% terminal width>…

        The spinner overwrites the two lines printed by on_thinking() on first call.
        When thinking ends, _end_thinking_compact() cancels this task and replaces
        the two lines with the completed format:
          Line 1: 🧠 Thought (3.5s)
          Line 2: —> <snippet truncated to 80% terminal width>…
        """
        try:
            while True:
                await asyncio.sleep(0.1)
                self._frame_idx += 1
                frame = _FRAMES[self._frame_idx % len(_FRAMES)]
                elapsed = time.monotonic() - self._thinking_start

                # Build a live snippet from the most recent thinking content
                raw = "".join(self._thinking_buf).replace("\n", " ").strip()
                tw = _terminal_width()
                # Reserve space for the prefix "⠋⠋ " (4 cols) and ellipsis
                snippet_budget = max(20, int(tw * 0.80) - 4)
                snippet = _truncate_by_width(raw, snippet_budget) if raw else "…"

                # Rewrite both lines: move up 1 line (cursor is after line 2)
                buf = (
                    f"\033[2A"  # move up 2 lines to line 1
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
            self._thinking_spinner_task = None

    def _stop_thinking_spinner(self) -> None:
        """Cancel the thinking spinner task (does NOT erase the line)."""
        if self._thinking_spinner_task and not self._thinking_spinner_task.done():
            self._thinking_spinner_task.cancel()
        self._thinking_spinner_task = None

    # ── rich.Live text rendering ─────────────────────────────────────────────

    def _refresh_live(self) -> None:
        """No-op: kept for API compatibility."""

    def _stop_live(self, finalize: bool = False) -> None:
        """Finalize a text block: render the buffered content via rich.Markdown.

        Text was buffered (not streamed) by on_text(), so finalize is a clean
        single render with no scroll-back / cursor math — immune to races with
        the concurrent tool spinner and _overwrite_async().
        """
        if self._live is not None:
            self._live.stop()
            self._live = None

        if finalize and self._text_buf:
            # Ensure the generating spinner is gone BEFORE we print Markdown,
            # otherwise its line collides with the rendered text.
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
        # Stop the generating spinner so it does NOT carry its elapsed timer
        # over into the next iteration (case showed "⠸ Almost there...  124.3s"
        # at the start of iteration 2 because the old task was still running).
        self._stop_generating()
        self._stop_live(finalize=True)
        self._batch.clear()
        self._done_ids.clear()
        self._batch_size = 0
        self._overwrite_tasks.clear()

    async def flush_async(self) -> None:
        """Async version of flush: awaits any in-flight overwrite tasks first.

        Must be called instead of flush() when an event loop is running, so
        that _overwrite_async tasks (which update spinner rows in-place) finish
        before the final Markdown block scrolls the terminal.  Without this
        the last tool's spinner row stays frozen on screen.
        """
        if self._overwrite_tasks:
            # Wait for all pending overwrite coroutines to complete.
            # return_exceptions=True ensures we don't crash if one fails.
            await asyncio.gather(*self._overwrite_tasks, return_exceptions=True)
            self._overwrite_tasks.clear()
        self.flush()

    def flush(self) -> None:
        self._stop_waiting()
        self._stop_thinking_spinner()
        self._stop_spinner()
        self._stop_generating()
        self._stop_live(finalize=True)
        self._end_thinking_compact()
        self._batch.clear()
        self._done_ids.clear()
        self._batch_size = 0
        self._overwrite_tasks.clear()

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _ensure_agent_label(self) -> None:
        if not self._agent_label_printed:
            # Stop the waiting spinner and erase its line before printing
            # the agent label so the label starts on a clean line.
            self._stop_waiting()
            console.print()
            label = f"🤖 Vico{self._model_label}:"
            console.print(Text(label, style=AGENT_NAME_STYLE))
            console.print()
            self._agent_label_printed = True

    def _end_thinking_compact(self) -> None:
        """Stop the thinking spinner and replace the two-line block with the completed format.

        Completed format:
          Line 1: 🧠 Thought (3.5s)
          Line 2: —> <snippet truncated to 80% terminal width>…

        A single blank line follows the completed block to separate it from the
        next content (tool rows or response text).
        """
        if not self._thinking_active:
            return
        self._thinking_active = False
        self._stop_thinking_spinner()

        full = "".join(self._thinking_buf).replace("\n", " ").strip()
        self._thinking_buf.clear()

        elapsed = time.monotonic() - self._thinking_start

        import shutil

        term_w = shutil.get_terminal_size(fallback=(100, 24)).columns
        # Reserve space for the prefix "—> " (3 cols) and ellipsis
        snippet_budget = max(20, int(term_w * 0.80) - 3)
        summary = _truncate_by_width(full, snippet_budget) if full else "…"

        # Overwrite both spinner lines with the completed two-line format,
        # then add ONE blank line after (not two — caller must NOT add more).
        buf = (
            f"\033[2A"  # move up 2 lines to overwrite line 1
            f"\r{_CLEAR_LINE}"
            f"{_DIM}🧠 Thought ({elapsed:.1f}s){_RESET}\n"
            f"{_CLEAR_LINE}"
            f"{_DIM}—> {_ITALIC}{summary}{_RESET}\n"
            f"\n"  # single blank line after the completed block
        )
        _write(buf)

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
            + _fmt_footer(
                elapsed_s=elapsed,
                prompt_tokens=self._last_prompt_tokens,
                completion_tokens=self._last_completion_tokens,
                context_pct=stats.usage_percent,
            )
            + "\n"
        )
