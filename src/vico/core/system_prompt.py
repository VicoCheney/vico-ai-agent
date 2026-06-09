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
# Vico persona resolution
# ---------------------------------------------------------------------------


def _resolve_vico_persona(cwd: str, default_vico: Path) -> str:
    """Load the agent's persona from the highest-priority source.

    Priority: <cwd>/.vico/Vico.md > ~/.vico/Vico.md > project Vico.md
    If none exists, return empty string.
    """
    # 1. Project-level override (highest priority)
    project_vico = Path(cwd) / ".vico" / "Vico.md"
    if project_vico.exists():
        try:
            content = project_vico.read_text(encoding="utf-8").strip()
            if content:
                logger.debug("Using project-level Vico persona: %s", project_vico)
                return content
        except OSError:
            pass

    # 2. User-level override
    user_vico = Path.home() / ".vico" / "Vico.md"
    if user_vico.exists():
        try:
            content = user_vico.read_text(encoding="utf-8").strip()
            if content:
                logger.debug("Using user-level Vico persona: %s", user_vico)
                return content
        except OSError:
            pass

    # 3. Default from prompts directory
    if default_vico.exists():
        try:
            content = default_vico.read_text(encoding="utf-8").strip()
            if content:
                logger.debug("Using default Vico persona: %s", default_vico)
                return content
        except OSError:
            pass

    return ""


# ---------------------------------------------------------------------------
# User profile resolution
# ---------------------------------------------------------------------------


def _resolve_user_profile(cwd: str, default_user: Path) -> str:
    """Load the user's profile from the highest-priority source.

    Priority: <cwd>/.vico/User.md > ~/.vico/User.md > project User.md
    If none exists, return empty string.
    """
    # 1. Project-level override (highest priority)
    project_user = Path(cwd) / ".vico" / "User.md"
    if project_user.exists():
        try:
            content = project_user.read_text(encoding="utf-8").strip()
            if content:
                logger.debug("Using project-level User profile: %s", project_user)
                return content
        except OSError:
            pass

    # 2. User-level override
    user_user = Path.home() / ".vico" / "User.md"
    if user_user.exists():
        try:
            content = user_user.read_text(encoding="utf-8").strip()
            if content:
                logger.debug("Using user-level User profile: %s", user_user)
                return content
        except OSError:
            pass

    # 3. Default from prompts directory
    if default_user.exists():
        try:
            content = default_user.read_text(encoding="utf-8").strip()
            if content:
                logger.debug("Using default User profile: %s", default_user)
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
    variables["vico_content"] = _resolve_vico_persona(cwd, loader.prompts_dir / "Vico.md")

    # Resolve user_content: <cwd>/.vico/User.md > ~/.vico/User.md > prompts/User.md
    variables["user_content"] = _resolve_user_profile(cwd, loader.prompts_dir / "User.md")

    # One call renders Agent.md with all {% include %} and {{ }} resolved
    prompt = loader.render(variables)

    # Token budget check
    loader.check_token_budget(prompt)

    return prompt.strip()