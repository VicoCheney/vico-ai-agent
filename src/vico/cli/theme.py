"""
Terminal colour & style constants.

All ANSI escape sequences and Rich style names live here so the rest of the
rendering code uses symbolic names instead of raw escape strings.
"""

from __future__ import annotations

# ─── ANSI escape sequences ────────────────────────────────────────────────────
RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"
ITALIC = "\033[3m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
CYAN_BOLD = "\033[1;36m"
WHITE_BOLD = "\033[1;37m"
BRIGHT_BLK = "\033[90m"
UNDERLINE = "\033[4m"
CLEAR_LINE = "\033[2K"

# Braille spinner frames
FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

# ─── Rich style names ─────────────────────────────────────────────────────────
PRIMARY = "cyan"
SECONDARY = "bright_black"
SUCCESS = "green"
ERROR = "red"
WARNING = "yellow"
SEPARATOR_COLOR = "bright_black"
AGENT_NAME_STYLE = "cyan bold"
TOOL_NAME_STYLE = "magenta bold"
