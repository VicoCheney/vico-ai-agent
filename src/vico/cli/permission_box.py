"""Permission box rendering helpers."""

from __future__ import annotations

from vico.cli import theme
from vico.cli.render_utils import (
    PRIORITY_PARAM_KEYS,
    col_widths,
    collapse_to_single_line,
    pad_to_width,
    truncate_by_width,
    visible_width,
    wcslen,
)
from vico.core.types import ToolCall

_RESET = theme.RESET
_BOLD = theme.BOLD
_DIM = theme.DIM
_GREEN = theme.GREEN
_RED = theme.RED
_YELLOW = theme.YELLOW
_CYAN_BOLD = theme.CYAN_BOLD
_BRIGHT_BLK = theme.BRIGHT_BLK


def fmt_perm_param(tool_call: ToolCall, cwd: str = "") -> str:
    """Return the most informative single-line parameter string for this tool call."""
    if not tool_call.input:
        return ""
    for key in PRIORITY_PARAM_KEYS:
        if key in tool_call.input:
            val = str(tool_call.input[key])
            if key in ("path", "file") and cwd and val.startswith(cwd):
                val = val[len(cwd) :].lstrip("/\\")
            val = " ".join(line.strip() for line in val.splitlines() if line.strip())
            return val
    first_key = next(iter(tool_call.input))
    val = str(tool_call.input[first_key])
    val = " ".join(line.strip() for line in val.splitlines() if line.strip())
    return val


def _perm_box_line(content: str, inner_w: int) -> str:
    """Format a single content row inside the permission box, padded to inner_w columns."""
    visible = visible_width(content)
    pad = max(0, inner_w - visible)
    return f"{_BRIGHT_BLK}│{_RESET}  {content}{' ' * pad}  {_BRIGHT_BLK}│{_RESET}"


def build_permission_box(tool_call: ToolCall, cwd: str = "") -> list[str]:
    """Build the permission card lines (without trailing newlines)."""
    import shutil

    term_w = shutil.get_terminal_size(fallback=(100, 24)).columns
    _PERM_PARAM_MAX_COLS = max(40, int(term_w * 0.50))

    param_str = fmt_perm_param(tool_call, cwd)

    title_plain = " \U0001f510 Permission Required "
    tool_plain = f"Tool    {tool_call.name}"

    if param_str:
        is_cmd = "command" in tool_call.input
        raw_display = ("$ " + param_str) if is_cmd else param_str
        display_param = truncate_by_width(raw_display, _PERM_PARAM_MAX_COLS)
    else:
        display_param = ""

    param_plain = f"Run     {display_param}" if display_param else ""

    extra_rows_plain: list[str] = []
    extra_keys = [k for k in tool_call.input if k not in PRIORITY_PARAM_KEYS]
    for key in extra_keys[:2]:
        extra_rows_plain.append(f"{key}    {tool_call.input[key]}")

    all_content_plain = [tool_plain] + ([param_plain] if param_plain else []) + extra_rows_plain
    content_max = max(wcslen(s) for s in all_content_plain) if all_content_plain else 30

    title_min = wcslen(title_plain) + 1
    inner_w = max(content_max, title_min)

    dashes_right = max(2, inner_w + 3 - wcslen(title_plain))
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


def fmt_approval_summary(
    decision: str,
    tool_name: str,
    param: str,
) -> str:
    """Compact one-line summary shown after the user makes a decision."""
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

    _tool_col, _param_cols, _stat_col = col_widths()
    name_col = collapse_to_single_line(tool_name).ljust(_tool_col)
    param_col = pad_to_width(truncate_by_width(collapse_to_single_line(param), _param_cols), _param_cols)
    stat_r = stat.rjust(_stat_col)
    return (
        f"{icon_color}{icon}{_RESET}"
        f" {_CYAN_BOLD}{name_col}{_RESET}"
        f"  {_DIM}{param_col}{_RESET}"
        f"  {stat_color}{stat_r}{_RESET}"
    )
