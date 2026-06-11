"""Skill metadata and content types."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SkillMeta:
    """Parsed from SKILL.md frontmatter — lightweight, always in memory."""

    skill_id: str            # Directory name, used as unique identifier
    name: str                # Human-readable display name
    description: str         # Short description for model discovery (shown in system prompt JSON)
    argument_hint: str = ""  # Shown in /skills list, e.g. "[file-or-dir]"
    disable_model_invocation: bool = False  # If True, model cannot self-activate this skill
    user_invocable: bool = True             # If False, hidden from /skills list
    skill_dir: Path = field(default_factory=Path)


@dataclass
class SkillContent:
    """Full skill content — loaded on demand when a skill is activated."""

    meta: SkillMeta
    body: str  # Everything in SKILL.md below the frontmatter delimiter
