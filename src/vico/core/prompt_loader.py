"""Prompt template loader — Agent.md IS the system prompt template.

Architecture
------------
``Agent.md`` is a Jinja2 template that defines the complete system prompt.
It uses ``{% include "xxx.md" %}`` directives to pull in section fragments,
and ``{{ variable }}`` placeholders for runtime values (OS, CWD, etc.).

Directory layout under ``src/vico/prompts/``::

    Agent.md      — the system prompt template (Jinja2, infrastructure)
    Vico.md       — agent persona (user-overridable via ~/.vico/Vico.md or <cwd>/.vico/Vico.md)
    User.md       — user profile (user-overridable via ~/.vico/User.md or <cwd>/.vico/User.md)
    *.md          — section fragments included via ``{% include %}``

Loading strategy
----------------
- All files read once at startup (no hot-reload, no caching)
- Missing includes raise immediately (strict validation)
- Token budget checked after rendering

Usage::

    loader = PromptLoader()
    system_prompt = loader.render(variables)   # one call → full system prompt
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateSyntaxError, UndefinedError

from vico.utils.text_utils import estimate_tokens

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def _resolve_prompts_dir() -> Path:
    """Locate the prompts directory bundled inside the package.

    Prompts live at ``src/vico/prompts/`` (or the installed equivalent).
    """
    pkg_dir = Path(__file__).resolve().parent.parent  # vico package dir
    prompts = pkg_dir / "prompts"
    if not prompts.is_dir():
        raise PromptFileNotFoundError(
            f"Prompts directory not found: {prompts}"
        )
    return prompts


_PROMPTS_DIR = _resolve_prompts_dir()
# Raised from 4000 to 8000.  Actual rendered prompt (Agent.md + all includes
# + runtime variables) is ~1700 tokens; 8000 gives a realistic headroom for
# persona / user-profile overrides without triggering spurious warnings.
SYSTEM_PROMPT_TOKEN_BUDGET = 8000

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PromptError(Exception):
    """Base class for prompt loading errors."""


class PromptFileNotFoundError(PromptError):
    """A required prompt file is missing from disk."""


class PromptValidationError(PromptError):
    """Prompt template or metadata is invalid."""


class PromptTemplateError(PromptError):
    """Jinja2 template rendering failed."""


# ---------------------------------------------------------------------------
# PromptLoader
# ---------------------------------------------------------------------------


class PromptLoader:
    """Load and render the system prompt from ``Agent.md`` template.

    Agent.md is the system prompt itself — a Jinja2 template that uses
    ``{% include %}`` to compose section fragments and ``{{ var }}`` for
    runtime substitution.
    """

    def __init__(self, prompts_dir: Path | None = None) -> None:
        self._prompts_dir = prompts_dir or _PROMPTS_DIR
        self._included_paths: list[str] = []

        # Jinja2 environment with FileSystemLoader for {% include %} support
        self._jinja_env = Environment(
            loader=FileSystemLoader(str(self._prompts_dir)),
            keep_trailing_newline=True,
            undefined=StrictUndefined,
        )

        self._validate()

    @property
    def prompts_dir(self) -> Path:
        """The directory containing prompt template files."""
        return self._prompts_dir

    # ------------------------------------------------------------------
    # Startup validation
    # ------------------------------------------------------------------

    def _validate(self) -> None:
        """Validate Agent.md exists and all {% include %} targets exist."""
        template_path = self._prompts_dir / "Agent.md"
        if not template_path.exists():
            raise PromptFileNotFoundError(
                f"System prompt template not found: {template_path}"
            )

        raw = self._read_file(template_path)

        # Extract all {% include "path" %} targets
        self._included_paths = re.findall(
            r'{%\s*include\s+["\']([^"\']+)["\']\s*%}', raw
        )

        # Validate each included file exists
        missing: list[str] = []
        for inc_path in self._included_paths:
            if not (self._prompts_dir / inc_path).exists():
                missing.append(inc_path)

        if missing:
            raise PromptValidationError(
                "Agent.md references missing files via {% include %}:\n"
                + "\n".join(f"  - {p}" for p in missing)
                + f"\nPrompts directory: {self._prompts_dir}"
            )

        logger.debug(
            "PromptLoader: validated %d includes from %s",
            len(self._included_paths),
            self._prompts_dir,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def render(self, variables: dict[str, str]) -> str:
        """Render the complete system prompt from Agent.md template.

        This is the primary entry point — one call produces the full prompt.
        """
        try:
            template = self._jinja_env.get_template("Agent.md")
            return template.render(**variables)
        except TemplateSyntaxError as exc:
            raise PromptTemplateError(
                f"Jinja2 syntax error in Agent.md: {exc.message} (line {exc.lineno})"
            )
        except UndefinedError as exc:
            raise PromptTemplateError(
                f"Undefined variable in Agent.md: {exc.message}"
            )

    def check_token_budget(self, rendered: str) -> int:
        """Check token count against budget. Returns the count."""
        total = estimate_tokens(rendered)
        if total > SYSTEM_PROMPT_TOKEN_BUDGET:
            logger.warning(
                "System prompt exceeds budget: %d > %d tokens. "
                "Consider trimming or splitting sections.",
                total,
                SYSTEM_PROMPT_TOKEN_BUDGET,
            )
        return total

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _read_file(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise PromptFileNotFoundError(f"Prompt file not found: {path}")
        except PermissionError:
            raise PromptError(f"Cannot read prompt file (permission denied): {path}")


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_default_loader: PromptLoader | None = None


def get_loader() -> PromptLoader:
    """Return the module-level singleton ``PromptLoader`` instance."""
    global _default_loader
    if _default_loader is None:
        _default_loader = PromptLoader()
    return _default_loader
