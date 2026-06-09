"""
Configuration Reader
====================

Loads .vicorc.json + .env, building the full AgentConfig.

Two primary entry points:
  load_config(cwd=None)      → AgentConfig      (CLI startup, /model reload)
  lookup_provider(name)       → dict             (/model command runtime lookup)

Project root discovery:
  Walks upward from cwd (or os.getcwd()) looking for .vicorc.json first,
  then pyproject.toml as fallback.  This way Vico works correctly even when
  launched from a subdirectory.

Config structure in .vicorc.json
---------------------------------
  providers.<name>            — credentials + per-model hyperparameters
    api_key_env               — env var name for the API key
    base_url                  — API endpoint
    default_model             — fallback model name
    models.<model_name>       — per-model hyperparameter overrides
      max_tokens / max_completion_tokens
      temperature
      top_p
      stop
      thinking_enabled
      reasoning_effort        — DeepSeek only: "high" | "max"
      response_format         — "text" | "json_object"

  llm.default
    provider                  — which provider block to use
    model                     — model to activate (overrides provider.default_model)
    (temperature, max_tokens, etc. no longer live here — use providers.<name>.models)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from vico.core.types import AgentConfig, ContextConfig, LLMConfig, ToolsConfig


def _find_config_root(cwd: str | None = None) -> Path:
    """
    Discover the Vico configuration directory.

    Search order (first match wins):
      1. Walk upward from cwd looking for .vicorc.json or pyproject.toml
      2. Environment variable VICO_CONFIG_DIR
      3. Global user config: ~/.config/vico/
      4. Package source directory (for editable installs)
      5. Fall back to cwd (or os.getcwd())
    """
    current = Path(cwd) if cwd else Path.cwd()
    current = current.resolve()

    # 1. Walk up from cwd — project-specific override
    for ancestor in [current, *current.parents]:
        if (ancestor / ".vicorc.json").exists():
            return ancestor
        if (ancestor / "pyproject.toml").exists():
            return ancestor

    # 2. VICO_CONFIG_DIR environment variable
    env_dir = os.environ.get("VICO_CONFIG_DIR")
    if env_dir:
        env_path = Path(env_dir).expanduser().resolve()
        if (env_path / ".vicorc.json").exists():
            return env_path

    # 3. Global user config directory
    global_config = Path.home() / ".config" / "vico"
    if (global_config / ".vicorc.json").exists():
        return global_config

    # 4. Package source directory (works for editable installs)
    # Assumes layout: src/vico/__init__.py → src/ → project_root/
    # This is the standard `src`-layout used by this project.
    try:
        import vico  # noqa: F401

        pkg_file = Path(vico.__file__).resolve()
        src_dir = pkg_file.parent.parent  # src/vico/__init__.py → src/
        project_root = src_dir.parent  # src → project root
        if (project_root / ".vicorc.json").exists():
            return project_root
    except Exception:
        pass

    # 5. Nothing found — fall back to cwd
    return current


# Discover config root *once* at import time for load_dotenv.
# The explicit cwd passed to load_config() can override this per call,
# but lookup_provider() relies on this implicit root.
_CONFIG_ROOT = _find_config_root()

# Load .env from the discovered config root (non-fatal if missing)
load_dotenv(dotenv_path=_CONFIG_ROOT / ".env")


def _load_vicorc(root: Path) -> dict[str, Any]:
    """Load .vicorc.json from the given directory."""
    rc_path = root / ".vicorc.json"
    if rc_path.exists():
        try:
            return dict(json.loads(rc_path.read_text()))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in '{rc_path}': {exc}") from exc
        except OSError as exc:
            raise ValueError(f"Cannot read config '{rc_path}': {exc}") from exc
    return {}


# ─── Public API ───────────────────────────────────────────────────────────────


def load_config(cwd: str | None = None) -> AgentConfig:
    """Load and build the full agent configuration.

    Parameters
    ----------
    cwd : str | None
        Working directory where the agent was launched.  The config root
        (.vicorc.json / .env location) is discovered independently — they
        may differ when Vico is run from outside the project directory.
    """
    config_root = _find_config_root(cwd) if cwd else _CONFIG_ROOT
    working_dir = str(Path(cwd).resolve()) if cwd else os.getcwd()
    rc = _load_vicorc(config_root)

    llm = _parse_llm_config(rc)
    context = _parse_context_config(rc)
    tools = _parse_tools_config(rc)

    return AgentConfig(llm=llm, context=context, tools=tools, cwd=working_dir)


def lookup_provider(provider_name: str) -> dict[str, str]:
    """
    Look up a provider's config from .vicorc.json + .env.
    Used by /model command at runtime to resolve provider/model for switching.
    """
    rc = _load_vicorc(_CONFIG_ROOT)
    providers = rc.get("providers", {})

    provider = providers.get(provider_name.lower())
    if not provider:
        raise ValueError(
            f"Unknown provider '{provider_name}'. Supported: {', '.join(providers.keys()) if providers else 'none'}"
        )

    api_key_env = provider.get("api_key_env", f"{provider_name.upper()}_API_KEY")
    base_url = provider.get("base_url", "")
    api_key = os.environ.get(api_key_env, "")

    return {
        "provider": provider_name.lower(),
        "api_key": api_key,
        "api_key_env": api_key_env,
        "base_url": base_url,
    }


# ─── Internal parsers ────────────────────────────────────────────────────────


def _parse_llm_config(rc: dict[str, Any]) -> LLMConfig:
    """
    Parse llm.default section + resolve provider credentials from .env.

    Hyperparameter priority (highest → lowest):
      1. providers.<name>.models.<model_name>.*   — per-model overrides
      2. providers.<name>.*                        — provider-level defaults
      3. LLMConfig field defaults                  — code defaults
    """
    llm_section = rc.get("llm", {}).get("default", {})
    providers = rc.get("providers", {})

    provider_name = llm_section.get("provider", "deepseek").lower()

    provider_cfg = providers.get(provider_name, {})
    if not provider_cfg:
        raise ValueError(
            f"Provider '{provider_name}' not found in .vicorc.json.\n"
            f"Available providers: {', '.join(providers.keys()) if providers else 'none'}"
        )

    api_key_env = provider_cfg.get("api_key_env", f"{provider_name.upper()}_API_KEY")
    api_key = os.environ.get(api_key_env, "")
    if not api_key:
        raise ValueError(
            f"Missing API key for provider '{provider_name}'.\n"
            f"Set {api_key_env} in your .env file.\n"
            "See .env.example for reference."
        )

    # Resolve which model is active
    model = llm_section.get("model", provider_cfg.get("default_model", ""))

    # Per-model hyperparams: providers.<name>.models.<model_name>.*
    model_params: dict[str, Any] = provider_cfg.get("models", {}).get(model, {})

    def _get(key: str, default: Any) -> Any:
        """Read from per-model params first, then provider-level, then default."""
        if key in model_params:
            return model_params[key]
        if key in provider_cfg:
            return provider_cfg[key]
        return default

    return LLMConfig(
        provider=provider_name,
        api_key=api_key,
        base_url=provider_cfg.get("base_url", ""),
        model=model,
        max_tokens=_get("max_tokens", _get("max_completion_tokens", 131072)),
        temperature=_get("temperature", 1.0),
        top_p=_get("top_p", None),
        stop=_get("stop", None),
        thinking_enabled=_get("thinking_enabled", True),
        # Collect provider-specific knobs into provider_options so that
        # the generic LLMConfig stays free of vendor-specific fields.
        provider_options={
            k: _get(k, default)
            for k, default in {
                "reasoning_effort": "max",   # DeepSeek only
                "response_format": "text",   # optional for any provider
            }.items()
        },
    )


def _parse_context_config(rc: dict[str, Any]) -> ContextConfig:
    """Parse context section from .vicorc.json.

    Note: defaults here intentionally differ from ContextConfig dataclass defaults.
    ContextConfig defaults are conservative (for library use); these defaults are
    tuned for production CLI use with large-context models (e.g. MiMo 1M ctx).
    """
    section = rc.get("context", {})
    return ContextConfig(
        max_tokens=section.get("max_tokens", 1000000),
        reserve_tokens=section.get("reserve_tokens", 131072),
        compression_threshold=section.get("compression_threshold", 0.85),
    )


def _parse_tools_config(rc: dict[str, Any]) -> ToolsConfig:
    """Parse tools section from .vicorc.json."""
    section = rc.get("tools", {})
    return ToolsConfig(
        auto_approve=section.get("auto_approve", ["low"]),
        timeout_ms=section.get("timeout_ms", 30000),
    )
