"""Low-level rendering utilities shared by the terminal renderer."""

from __future__ import annotations

import re
import sys
import unicodedata

__all__ = [
    "PRIORITY_PARAM_KEYS",
    "char_width",
    "collapse_to_single_line",
    "is_tty",
    "pad_to_width",
    "strip_internal_tags",
    "truncate_by_width",
    "visible_width",
    "wcslen",
    "write",
    "TypewriterTracker",
]

# ─── Shared constants ─────────────────────────────────────────────────────────

# Priority order of parameter keys to display in tool call summaries.
# Shared by permission_box.py and tool_format.py to keep behaviour consistent.
PRIORITY_PARAM_KEYS: tuple[str, ...] = ("command", "skill_id", "path", "query", "pattern", "url", "file")


# ─── I/O helpers ─────────────────────────────────────────────────────────────


def write(s: str) -> None:
    """Write directly to stdout and flush immediately."""
    sys.stdout.write(s)
    sys.stdout.flush()


def is_tty() -> bool:
    """Return True if stdout is an interactive terminal."""
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


# ─── Unicode display-width helpers ───────────────────────────────────────────


def char_width(ch: str) -> int:
    """Terminal display width of a single character.

    W/F → 2 cols. Ambiguous (A) → 2 cols (most modern terminals).
    Exception: U+2026 '…' is eaw=A but renders as 1 col everywhere.
    Variation selectors and combining marks → 0 cols.
    """
    cp = ord(ch)
    if 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF:
        return 0
    cat = unicodedata.category(ch)
    if cat in ("Mn", "Me", "Cf"):
        return 0
    if cp == 0x2026:
        return 1
    eaw = unicodedata.east_asian_width(ch)
    return 2 if eaw in ("W", "F", "A") else 1


def wcslen(s: str) -> int:
    """Terminal display width of a plain (no ANSI) string."""
    return sum(char_width(ch) for ch in s)


def truncate_by_width(s: str, max_width: int) -> str:
    """Truncate *s* so its display width ≤ *max_width*, appending '…' if truncated."""
    w = 0
    for i, ch in enumerate(s):
        cw = char_width(ch)
        if cw == 0:
            continue
        if w + cw > max_width - 1:
            return s[:i] + "…"
        w += cw
    return s


def pad_to_width(s: str, target_cols: int) -> str:
    """Pad *s* with spaces so its display width equals *target_cols*."""
    current = wcslen(s)
    if current >= target_cols:
        return s
    return s + " " * (target_cols - current)


def visible_width(s: str) -> int:
    """Terminal display width of a string that may contain ANSI escape codes."""
    plain = re.sub(r"\033\[[0-9;]*[mK]", "", s)
    return wcslen(plain)


# ─── Text helpers ─────────────────────────────────────────────────────────────


def strip_internal_tags(s: str) -> str:
    """Remove planner/internal XML-ish tags that should never reach the user."""
    for tag in ("plan_summary", "plan", "thinking", "tool_invocation", "tool_call", "function_call", "invoke"):
        out_pattern = rf"<{tag}\b[^>]*>[\s\S]*?</{tag}>"
        s = re.sub(out_pattern, "", s)
    s = re.sub(
        r"<(?:tool_invocation|tool_call|function_call|invoke)\b[^>]*/?>",
        "",
        s,
    )
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip("\n")


def collapse_to_single_line(s: str) -> str:
    """Collapse multi-line string to a single space-separated line."""
    return " ".join(part.strip() for part in s.splitlines() if part.strip())


# ─── TypewriterTracker ────────────────────────────────────────────────────────


class TypewriterTracker:
    """Track physical terminal rows occupied by typewriter output across streaming chunks.

    Models deferred-wrap terminals: writing exactly term_w chars leaves the
    cursor at the last column WITHOUT advancing to the next row.
    """

    def __init__(self, term_w: int) -> None:
        self._term_w = max(1, term_w)
        self._col: int = 0
        self._completed_rows: int = 0

    def feed(self, text: str) -> None:
        """Ingest a raw text chunk (may contain newlines and ANSI escapes)."""
        if not text:
            return
        plain = re.sub(r"\033\[[0-9;]*[mK]", "", text)
        for ch in plain:
            if ch == "\n":
                self._completed_rows += 1
                self._col = 0
                continue
            if ch == "\r":
                self._col = 0
                continue
            cw = char_width(ch)
            if cw == 0:
                continue
            if self._col + cw > self._term_w:
                self._completed_rows += 1
                self._col = cw
            else:
                self._col += cw

    @property
    def rows_above(self) -> int:
        """Rows above the cursor that contain typewriter content."""
        return self._completed_rows

    @property
    def lines(self) -> int:
        """Total physical rows occupied (always ≥ 1)."""
        return self._completed_rows + 1
