"""Runtime support for Skill activation and instruction injection."""

from __future__ import annotations

import html
import logging
import re
from collections.abc import Callable

from vico.core.context_manager import ContextManager
from vico.core.skill_provider import ISkillProvider
from vico.skills.types.meta import SkillContent, SkillMeta
from vico.tools.types.execution import ToolResult

logger = logging.getLogger(__name__)

_USE_SKILL_RE = re.compile(r"<use_skill>\s*([^<\s]+)\s*</use_skill>", re.IGNORECASE)


class SkillRuntime:
    """Owns Skill activation rules and context injection."""

    def __init__(
        self,
        skill_loader: ISkillProvider | None,
        context: ContextManager,
        cwd: str,
        on_skill_activated: Callable[[SkillMeta], None] | None = None,
    ) -> None:
        self._skill_loader = skill_loader
        self._context = context
        self._cwd = cwd
        self._on_skill_activated = on_skill_activated

    @property
    def enabled(self) -> bool:
        return self._skill_loader is not None

    def format_instructions(self, content: SkillContent, arguments: str = "") -> str:
        args_block = f"\n<skill_arguments>\n{arguments}\n</skill_arguments>\n" if arguments else ""
        skill_id = html.escape(content.meta.skill_id, quote=True)
        name = html.escape(content.meta.name, quote=True)
        source = html.escape(content.meta.source, quote=True)
        path = html.escape(str(content.meta.skill_dir), quote=True)
        body = (
            content.body
            .replace("$ARGUMENTS", arguments)
            .replace("${VICO_SKILL_DIR}", str(content.meta.skill_dir))
            .replace("${VICO_CWD}", self._cwd)
        )
        return (
            f'<skill_instructions id="{skill_id}" '
            f'name="{name}" '
            f'source="{source}" '
            f'path="{path}">\n'
            f"{args_block}"
            f"{body}\n"
            "</skill_instructions>"
        )

    def inject_by_id(
        self,
        skill_id: str,
        arguments: str = "",
        *,
        bypass_manual_only: bool = False,
    ) -> bool:
        if not self._skill_loader:
            return False

        content = self._skill_loader.get_skill_content(skill_id)
        if not content:
            return False

        if content.meta.disable_model_invocation and not bypass_manual_only:
            logger.info("Skill %r has disable_model_invocation=True.", skill_id)
            self._context.add_user_message(
                f"[System] Skill '{skill_id}' can only be activated by the user "
                f"via `/skill {skill_id}`. Please proceed without it."
            )
            return True

        self._context.add_user_message(self.format_instructions(content, arguments=arguments))
        logger.info("Injected skill %r (%d chars).", skill_id, len(content.body))
        if self._on_skill_activated:
            self._on_skill_activated(content.meta)
        return True

    def inject_from_legacy_tag(self, text: str) -> bool:
        """Detect legacy <use_skill>ID</use_skill> and inject the Skill body."""
        match = _USE_SKILL_RE.search(text)
        if not match:
            return False

        skill_id = match.group(1).strip()
        if self.inject_by_id(skill_id):
            return True

        logger.warning("Skill %r requested but not found.", skill_id)
        self._context.add_user_message(
            f"[System] Skill '{skill_id}' was not found. Please proceed without it."
        )
        return True

    def inject_from_tool_result(self, result: ToolResult) -> None:
        """Inject Skill instructions requested by the activate_skill tool."""
        if not result.success:
            return

        activation = result.meta.skill_activation
        if activation is None and result.metadata.get("skill_activation"):
            result.__post_init__()
            activation = result.meta.skill_activation
        if activation is None or not activation.skill_id:
            return

        self.inject_by_id(activation.skill_id, arguments=activation.arguments)
