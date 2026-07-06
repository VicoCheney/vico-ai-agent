"""Terminal utility — geometry helpers shared across layers."""

from __future__ import annotations

import shutil


def terminal_width() -> int:
    """Return current terminal width (columns), default 100."""
    return shutil.get_terminal_size(fallback=(100, 24)).columns


def col_widths() -> tuple[int, int, int, int]:
    """Return tool row widths: command 15%, params 50%, status 20%, blank 15%."""
    # Keep tool rows below the terminal's last column. Many terminals auto-wrap
    # when a line reaches the exact width, which breaks cursor-up row rewrites.
    available = max(20, terminal_width() - 2)
    tool_col = max(8, int(available * 0.15))
    param_cols = max(12, int(available * 0.50))
    stat_col = max(8, int(available * 0.20))

    overflow = tool_col + param_cols + stat_col - available
    if overflow > 0:
        shrink = min(overflow, max(0, param_cols - 8))
        param_cols -= shrink
        overflow -= shrink
    if overflow > 0:
        shrink = min(overflow, max(0, stat_col - 6))
        stat_col -= shrink
        overflow -= shrink
    if overflow > 0:
        shrink = min(overflow, max(0, tool_col - 6))
        tool_col -= shrink

    blank_col = max(0, available - tool_col - param_cols - stat_col)
    return tool_col, param_cols, stat_col, blank_col
