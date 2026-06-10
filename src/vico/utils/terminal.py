"""Terminal utility — geometry helpers shared across layers."""

from __future__ import annotations

import shutil


def terminal_width() -> int:
    """Return current terminal width (columns), default 100."""
    return shutil.get_terminal_size(fallback=(100, 24)).columns


def col_widths() -> tuple[int, int, int]:
    """Return (tool_col, param_cols, stat_col) based on current terminal width."""
    term_w = terminal_width()
    tool_col = max(8, int(term_w * 0.05))
    stat_col = max(15, int(term_w * 0.15))
    param_cols = max(48, int(term_w * 0.60))
    return tool_col, param_cols, stat_col
