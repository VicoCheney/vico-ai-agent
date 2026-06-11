"""Skill-related protocols for the core layer.

Defines the ``ISkillProvider`` protocol so that ``core`` modules can depend
on the interface without importing the concrete ``skills.loader`` module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from vico.skills.types.meta import SkillContent, SkillMeta


@runtime_checkable
class ISkillProvider(Protocol):
    """Read-only interface to the skill subsystem consumed by the core layer."""

    def get_all_metas(self) -> list[SkillMeta]:
        """Return metadata for all discovered skills."""
        ...

    def get_skill_content(self, skill_id: str) -> SkillContent | None:
        """Load and return the full content of a skill by id, or None if not found."""
        ...
