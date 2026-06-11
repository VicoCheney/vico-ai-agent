"""Prompt template loader — Agent.md is the system prompt template.

Agent.md is a Jinja2 template that uses ``{% include %}`` to compose section
fragments and ``{{ variable }}`` for runtime substitution.

Directory layout under ``src/vico/prompts/``::

    Agent.md      — system prompt template (Jinja2)
    Vico.md       — agent persona (overridable via ~/.vico/Vico.md)
    User.md       — user profile (overridable via ~/.vico/User.md)
    *.md          — section fragments included via ``{% include %}``
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateSyntaxError, UndefinedError

from vico.exceptions import PromptError, PromptFileNotFoundError, PromptTemplateError, PromptValidationError
from vico.utils.text_utils import estimate_tokens

logger = logging.getLogger(__name__)


def _resolve_prompts_dir() -> Path:
    pkg_dir = Path(__file__).resolve().parent.parent
    prompts = pkg_dir / "prompts"
    if not prompts.is_dir():
        raise PromptFileNotFoundError(f"Prompts directory not found: {prompts}")
    return prompts


_PROMPTS_DIR: Path | None = None
# Rendered prompt is ~1700 tokens; 8000 gives headroom for persona/user-profile overrides.
SYSTEM_PROMPT_TOKEN_BUDGET = 8000


class PromptLoader:
    """Load and render the system prompt from ``Agent.md`` template."""

    def __init__(self, prompts_dir: Path | None = None) -> None:
        self._prompts_dir = prompts_dir or _resolve_prompts_dir()
        self._included_paths: list[str] = []

        self._jinja_env = Environment(
            loader=FileSystemLoader(str(self._prompts_dir)),
            keep_trailing_newline=True,
            undefined=StrictUndefined,
        )

        self._validate()

    @property
    def prompts_dir(self) -> Path:
        return self._prompts_dir

    def _validate(self) -> None:
        """Validate Agent.md exists and all {% include %} targets exist."""
        template_path = self._prompts_dir / "Agent.md"
        if not template_path.exists():
            raise PromptFileNotFoundError(f"System prompt template not found: {template_path}")

        raw = self._read_file(template_path)
        self._included_paths = re.findall(r'{%\s*include\s+["\']([^"\']+)["\']\s*%}', raw)

        missing = [p for p in self._included_paths if not (self._prompts_dir / p).exists()]
        if missing:
            raise PromptValidationError(
                "Agent.md references missing files via {% include %}:\n"
                + "\n".join(f"  - {p}" for p in missing)
                + f"\nPrompts directory: {self._prompts_dir}"
            )

        logger.debug("PromptLoader: validated %d includes.", len(self._included_paths))

    def render(self, variables: dict[str, str]) -> str:
        """Render the complete system prompt from Agent.md template."""
        try:
            template = self._jinja_env.get_template("Agent.md")
            return template.render(**variables)
        except TemplateSyntaxError as exc:
            raise PromptTemplateError(
                f"Jinja2 syntax error in Agent.md: {exc.message} (line {exc.lineno})"
            )
        except UndefinedError as exc:
            raise PromptTemplateError(f"Undefined variable in Agent.md: {exc.message}")

    def check_token_budget(self, rendered: str) -> int:
        """Check token count against budget. Returns the count."""
        total = estimate_tokens(rendered)
        if total > SYSTEM_PROMPT_TOKEN_BUDGET:
            logger.warning(
                "System prompt exceeds budget: %d > %d tokens.",
                total,
                SYSTEM_PROMPT_TOKEN_BUDGET,
            )
        return total

    @staticmethod
    def _read_file(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise PromptFileNotFoundError(f"Prompt file not found: {path}")
        except PermissionError:
            raise PromptError(f"Cannot read prompt file (permission denied): {path}")


@lru_cache(maxsize=1)
def get_loader() -> PromptLoader:
    """Return the module-level singleton PromptLoader.

    Uses lru_cache so it can be invalidated in tests via get_loader.cache_clear().
    """
    return PromptLoader()
