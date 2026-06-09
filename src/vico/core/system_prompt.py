"""System Prompt Builder for the Vico AI Agent.

Agent.md IS the system prompt — a Jinja2 template that composes section
fragments via {% include %} and substitutes runtime variables via {{ }}.

Vico persona resolution
-----------------------
The Agent's persona is loaded from the first available source:
  1. <cwd>/.vico/Vico.md  — project-level override (highest priority)
  2. ~/.vico/Vico.md       — user-level override
  3. project Vico.md       — default (bundled in prompts dir)

Public API:
    build_system_prompt(cwd: str) -> str
"""

from __future__ import annotations

import logging
import os
import platform
from datetime import UTC, datetime
from pathlib import Path

from vico.core.prompt_loader import get_loader

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Persona / profile file resolution
# ---------------------------------------------------------------------------


def _resolve_prompt_file(filename: str, cwd: str, default_path: Path) -> str:
    """Load a persona/profile Markdown file from the highest-priority source.

    Search order (first non-empty file wins):
      1. ``<cwd>/.vico/<filename>``  — project-level override
      2. ``~/.vico/<filename>``      — user-level override
      3. *default_path*              — bundled default in the prompts directory

    Returns the file's text content (stripped), or an empty string if none
    of the candidates exists or all are empty.
    """
    candidates: list[Path] = [
        Path(cwd) / ".vico" / filename,
        Path.home() / ".vico" / filename,
        default_path,
    ]
    for path in candidates:
        if path.exists():
            try:
                content = path.read_text(encoding="utf-8").strip()
                if content:
                    logger.debug("Loaded prompt file %s from %s", filename, path)
                    return content
            except OSError:
                pass
    return ""


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------


def _get_shell() -> str:
    return os.environ.get("SHELL", "/bin/sh" if platform.system() != "Windows" else "cmd.exe")


def _make_variables(cwd: str) -> dict[str, str]:
    """Collect all runtime variables for Jinja2 template rendering.

    # Removed _get_git_info() call: it spawned two synchronous subprocess
    # calls (git rev-parse + git status) blocking the asyncio event loop
    # for up to 4 seconds on slow/network-mounted repos.  Git context is not
    # essential for the agent's core operation and has been removed entirely.
    """
    os_name = platform.system()
    return {
        "OS_NAME": os_name,
        "OS_NOTE": "macOS — remember `sed -i ''`" if os_name == "Darwin" else "Linux",
        "SHELL": _get_shell(),
        "CWD": cwd,
        "NOW": datetime.now(UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def build_system_prompt(cwd: str) -> str:
    """Render the complete system prompt from Agent.md template.

    Agent.md is a Jinja2 template that composes the final system prompt
    by including section fragments and substituting runtime variables.
    """
    variables = _make_variables(cwd)
    loader = get_loader()

    # Resolve vico_content: <cwd>/.vico/Vico.md > ~/.vico/Vico.md > prompts/Vico.md
    variables["vico_content"] = _resolve_prompt_file("Vico.md", cwd, loader.prompts_dir / "Vico.md")

    # Resolve user_content: <cwd>/.vico/User.md > ~/.vico/User.md > prompts/User.md
    variables["user_content"] = _resolve_prompt_file("User.md", cwd, loader.prompts_dir / "User.md")

    # One call renders Agent.md with all {% include %} and {{ }} resolved
    prompt = loader.render(variables)

    # Token budget check
    loader.check_token_budget(prompt)

    return prompt.strip()