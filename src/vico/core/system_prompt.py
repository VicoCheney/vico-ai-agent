"""System Prompt Builder for the Vico AI Agent.

Agent.md is a Jinja2 template that composes section fragments via {% include %}
and substitutes runtime variables via {{ }}.

Persona resolution order (first non-empty wins):
  1. <cwd>/.vico/Vico.md
  2. ~/.vico/Vico.md
  3. prompts/Vico.md  (bundled default)
"""

from __future__ import annotations

import json
import logging
import os
import platform
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from vico.core.prompt_loader import get_loader
from vico.core.skill_provider import ISkillProvider

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def _resolve_prompt_file(filename: str, cwd: str, default_path: Path) -> str:
    """Load a persona/profile Markdown file from the highest-priority source.

    Search order: <cwd>/.vico/<filename> → ~/.vico/<filename> → default_path.
    Returns the file's text content, or "" if none found.
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
                    logger.debug("Loaded %s from %s", filename, path)
                    return content
            except OSError:
                pass
    return ""


def _get_shell() -> str:
    return os.environ.get("SHELL", "/bin/sh" if platform.system() != "Windows" else "cmd.exe")


def _make_variables(cwd: str) -> dict[str, str]:
    """Collect runtime variables for Jinja2 template rendering."""
    os_name = platform.system()
    return {
        "OS_NAME": os_name,
        "OS_NOTE": "macOS — remember `sed -i ''`" if os_name == "Darwin" else "Linux",
        "SHELL": _get_shell(),
        "CWD": cwd,
        "NOW": datetime.now(UTC).isoformat(),
    }


def _build_skills_summary(skill_loader: ISkillProvider) -> str:
    """Build the skills JSON block injected into the system prompt.

    Returns "" if no user-invocable skills are available.
    """
    from vico.skills.types.meta import SkillMeta

    metas: list[SkillMeta] = [m for m in skill_loader.get_all_metas() if m.user_invocable]
    if not metas:
        return ""

    skills_list = [
        {
            "id": m.skill_id,
            "name": m.name,
            "description": m.description,
            **({"argument_hint": m.argument_hint} if m.argument_hint else {}),
            **({"disable_model_invocation": True} if m.disable_model_invocation else {}),
        }
        for m in metas
    ]

    json_block = json.dumps(skills_list, ensure_ascii=False, indent=2)

    return f"""\
## 🎯 Available Skills

You have the following skills available. Each skill contains specialized instructions
for a specific type of task. When the user's request clearly matches a skill's purpose,
respond with a `<use_skill>` tag to request loading its full instructions.

```json
{json_block}
```

**How to activate a skill:**
- Output `<use_skill>SKILL_ID</use_skill>` anywhere in your response text.
- The system will load the full skill instructions and inject them into the next turn.
- Skills marked `"disable_model_invocation": true` can only be triggered by the user (via `/skill SKILL_ID`).
- You may activate at most one skill per turn.
- Only activate a skill when you are confident it matches the current task."""


def build_system_prompt(cwd: str, skill_loader: ISkillProvider | None = None) -> str:
    """Render the complete system prompt from Agent.md template."""
    variables = _make_variables(cwd)
    loader = get_loader()

    variables["vico_content"] = _resolve_prompt_file("Vico.md", cwd, loader.prompts_dir / "Vico.md")
    variables["user_content"] = _resolve_prompt_file("User.md", cwd, loader.prompts_dir / "User.md")
    variables["skills_summary"] = _build_skills_summary(skill_loader) if skill_loader else ""

    prompt = loader.render(variables)
    loader.check_token_budget(prompt)

    return prompt.strip()
