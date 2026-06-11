"""
Skill Loader — scans directories for SKILL.md files and parses them.

Search paths (highest priority first):
  1. <cwd>/.vico/skills/<name>/SKILL.md
  2. <cwd>/.agents/skills/<name>/SKILL.md   (cross-tool compatibility alias)
  3. ~/.vico/skills/<name>/SKILL.md
  4. ~/.agents/skills/<name>/SKILL.md

Skills with the same id are shadowed: higher-priority path wins.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from vico.skills.types.meta import SkillContent, SkillMeta

logger = logging.getLogger(__name__)

# Regex to split SKILL.md into frontmatter and body
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL)

# Regex to parse a single frontmatter line
_KV_RE = re.compile(r"^(\w[\w\-]*)\s*:\s*(.*)")


def _parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    """Split SKILL.md content into (frontmatter_dict, body).

    Supports simple key: value and key: | multiline blocks.
    Returns ({}, raw) if no frontmatter block is found.
    """
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        return {}, raw

    fm_text = m.group(1)
    body = m.group(2)

    result: dict[str, str] = {}
    current_key: str | None = None
    multiline_lines: list[str] = []

    for line in fm_text.splitlines():
        kv = _KV_RE.match(line)
        if kv:
            if current_key and multiline_lines:
                result[current_key] = "\n".join(multiline_lines).strip()
                multiline_lines = []
                current_key = None

            key = kv.group(1).lower().replace("-", "_")
            value = kv.group(2).strip()

            if value == "|":
                current_key = key
            else:
                result[key] = value

        elif current_key is not None:
            multiline_lines.append(line.lstrip("  ") if line.startswith("  ") else line)

    # Flush last multiline key
    if current_key and multiline_lines:
        result[current_key] = "\n".join(multiline_lines).strip()

    return result, body


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in ("true", "yes", "1")


class SkillLoader:
    """Discovers and parses Skill packs from the filesystem.

    Skills are SKILL.md files in subdirectories of the search paths.
    All skills are discovered at construction time; bodies are loaded lazily.
    """

    def __init__(self, cwd: str) -> None:
        self._cwd = Path(cwd).resolve()
        self._skills: dict[str, SkillMeta] = {}
        self._scan()

    # ─── Public API ───────────────────────────────────────────────────────────

    def get_all_metas(self) -> list[SkillMeta]:
        """Return metadata for all discovered skills."""
        return list(self._skills.values())

    def get_skill_content(self, skill_id: str) -> SkillContent | None:
        """Load and return the full content of a skill by id. Returns None if not found."""
        meta = self._skills.get(skill_id)
        if not meta:
            skill_id_lower = skill_id.lower().replace(" ", "-")
            for sid, m in self._skills.items():
                if sid == skill_id_lower or m.name.lower().replace(" ", "-") == skill_id_lower:
                    meta = m
                    break

        if not meta:
            return None

        skill_md = meta.skill_dir / "SKILL.md"
        try:
            raw = skill_md.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Cannot read SKILL.md for skill %r: %s", meta.skill_id, exc)
            return None

        _, body = _parse_frontmatter(raw)
        return SkillContent(meta=meta, body=body.strip())

    def __len__(self) -> int:
        return len(self._skills)

    # ─── Discovery ────────────────────────────────────────────────────────────

    def _scan(self) -> None:
        """Scan all search paths for skill directories. Lower-priority paths
        are skipped for a skill_id that was already found at higher priority."""
        search_dirs = self._build_search_dirs()
        for base_dir in search_dirs:
            self._scan_dir(base_dir)

        if self._skills:
            logger.debug(
                "SkillLoader: found %d skill(s): %s",
                len(self._skills),
                ", ".join(self._skills.keys()),
            )
        else:
            logger.debug("SkillLoader: no skills found in any search path.")

    def _build_search_dirs(self) -> list[Path]:
        """Return the ordered list of directories to search for skill packs."""
        home = Path.home()
        return [
            self._cwd / ".vico" / "skills",
            home / ".vico" / "skills",
        ]

    def _scan_dir(self, base: Path) -> None:
        """Scan a single base directory for <name>/SKILL.md entries."""
        if not base.is_dir():
            return
        for entry in sorted(base.iterdir()):
            if not entry.is_dir():
                continue
            skill_id = entry.name
            if skill_id in self._skills:
                continue
            meta = self._parse_skill_md(entry)
            if meta:
                self._skills[skill_id] = meta

    def _parse_skill_md(self, skill_dir: Path) -> SkillMeta | None:
        """Parse a SKILL.md file and return a SkillMeta, or None on failure."""
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            return None

        try:
            raw = skill_md.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Cannot read %s: %s", skill_md, exc)
            return None

        fm, _ = _parse_frontmatter(raw)

        name = fm.get("name", skill_dir.name).strip()
        description = fm.get("description", "").strip()
        if not description:
            logger.warning("Skill %r has no description — will not appear in model context.", skill_dir.name)
            return None

        return SkillMeta(
            skill_id=skill_dir.name,
            name=name,
            description=description,
            argument_hint=fm.get("argument_hint", fm.get("argument-hint", "")).strip(),
            disable_model_invocation=_parse_bool(fm.get("disable_model_invocation", fm.get("disable-model-invocation", "false"))),
            user_invocable=_parse_bool(fm.get("user_invocable", fm.get("user-invocable", "true"))),
            skill_dir=skill_dir,
        )
