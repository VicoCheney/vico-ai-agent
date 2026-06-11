"""Tool call formatting and stats footer helpers."""

from __future__ import annotations

import re

from vico.cli import theme
from vico.cli.render_utils import (
    PRIORITY_PARAM_KEYS,
    collapse_to_single_line,
    pad_to_width,
    truncate_by_width,
    wcslen,
)
from vico.tools.types.call import ToolCall
from vico.tools.types.execution import ToolResult
from vico.utils.terminal import col_widths

_RESET = theme.RESET
_BOLD = theme.BOLD
_DIM = theme.DIM
_GREEN = theme.GREEN
_RED = theme.RED
_YELLOW = theme.YELLOW
_CYAN_BOLD = theme.CYAN_BOLD
_BRIGHT_BLK = theme.BRIGHT_BLK


def tool_label(tool_call: ToolCall, cwd: str = "") -> tuple[str, str]:
    """Return (tool_name, param_str) where param is always a single line."""
    if not tool_call.input:
        return tool_call.name, ""

    def _normalise(key: str, raw: str) -> str:
        val = raw
        if key in ("path", "file") and cwd and val.startswith(cwd):
            val = val[len(cwd) :].lstrip("/\\")
        return collapse_to_single_line(val)

    for key in PRIORITY_PARAM_KEYS:
        if key in tool_call.input:
            return tool_call.name, _normalise(key, str(tool_call.input[key]))
    first_key = next(iter(tool_call.input))
    return tool_call.name, _normalise(first_key, str(tool_call.input[first_key]))


def fmt_stat(result: ToolResult) -> str:
    approval = result.metadata.get("approval", "") if result.metadata else ""
    if approval:
        return approval
    if result.success:
        n = len(result.output.splitlines())
        return f"{n} ln" if n else "ok"
    err = result.error or "failed"
    m = re.search(r"exit(?:ed)?(?: with)?(?: code)?\s*(\d+)", err, re.I)
    return f"exit {m.group(1)}" if m else err[:8]


def fmt_running(frame: str, name: str, param: str) -> str:
    """Spinner row: <frame> <tool_col>  <param_cols>"""
    _tool_col, _param_cols, _ = col_widths()
    name_col = collapse_to_single_line(name).ljust(_tool_col)
    param_col = pad_to_width(truncate_by_width(collapse_to_single_line(param), _param_cols), _param_cols)
    return f"{_BRIGHT_BLK}{frame} {_RESET}{_CYAN_BOLD}{name_col}{_RESET}  {_DIM}{param_col}{_RESET}"


def fmt_done(success: bool, name: str, param: str, stat: str) -> str:
    """Done row: <icon> <tool_col>  <param_cols>  <stat_col>"""
    _tool_col, _param_cols, _stat_col = col_widths()
    icon_color = _GREEN if success else _RED
    icon = "✓" if success else "✗"
    name_col = collapse_to_single_line(name).ljust(_tool_col)
    param_col = pad_to_width(truncate_by_width(collapse_to_single_line(param), _param_cols), _param_cols)
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


def fmt_footer(elapsed_s: float, prompt_tokens: int, completion_tokens: int, context_pct: float) -> str:
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

    # ═ renders as 1 col on macOS Terminal
    inner_cols = wcslen(inner_plain)
    border = "\u2550" * inner_cols
    return (
        f"{_BRIGHT_BLK}\u2554{border}\u2557{_RESET}\n"
        f"{_BRIGHT_BLK}\u2551{_RESET}{inner_colored}{_BRIGHT_BLK}\u2551{_RESET}\n"
        f"{_BRIGHT_BLK}\u255a{border}\u255d{_RESET}"
    )
