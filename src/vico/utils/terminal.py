"""Terminal utility — geometry helpers shared across layers."""

from __future__ import annotations

import shutil


def terminal_width() -> int:
    """Return current terminal width (columns), default 100."""
    return shutil.get_terminal_size(fallback=(100, 24)).columns


def col_widths() -> tuple[int, int, int, int]:
    """Return tool row widths: command 15%, params 50%, status 20%, blank 15%."""
    available = max(40, terminal_width())
    tool_col = max(8, int(available * 0.15))
    param_cols = max(12, int(available * 0.50))
    stat_col = max(8, int(available * 0.20))
    blank_col = max(0, available - tool_col - param_cols - stat_col)
    return tool_col, param_cols, stat_col, blank_col
