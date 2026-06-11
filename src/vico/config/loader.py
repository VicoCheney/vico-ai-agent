"""
Configuration Loader
====================

Loads .vicorc.json + .env, building the full AgentConfig.

Entry points:
  load_config(cwd=None)   → AgentConfig  (CLI startup, /model reload)
  lookup_provider(name)   → dict         (/model command runtime lookup)

Config discovery walks upward from cwd looking for .vicorc.json, then
pyproject.toml as fallback. Falls back to VICO_CONFIG_DIR env var,
~/.config/vico/, or the package source directory.

Hyperparameter priority (highest → lowest):
  providers.<name>.models.<model_name>.*  — per-model overrides
  providers.<name>.*                       — provider-level defaults
  LLMConfig field defaults                 — code defaults
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from vico.config.types.config import (
    DEFAULT_MAX_TOKENS,
    AgentConfig,
    AgentLimits,
    ContextConfig,
    LLMConfig,
    ToolsConfig,
)
from vico.exceptions import ConfigError, ProviderAuthError, ProviderUnknownError


def _find_config_root(cwd: str | None = None) -> Path:
    """Discover the Vico configuration directory.

    Search order: walk up from cwd → VICO_CONFIG_DIR → ~/.config/vico/ →
    package source dir → cwd fallback.
    """
    current = Path(cwd).resolve() if cwd else Path.cwd().resolve()

    for ancestor in [current, *current.parents]:
        if (ancestor / ".vicorc.json").exists():
            return ancestor
        if (ancestor / "pyproject.toml").exists():
            return ancestor

    env_dir = os.environ.get("VICO_CONFIG_DIR")
    if env_dir:
        env_path = Path(env_dir).expanduser().resolve()
        if (env_path / ".vicorc.json").exists():
            return env_path

    global_config = Path.home() / ".config" / "vico"
    if (global_config / ".vicorc.json").exists():
        return global_config

    try:
        import vico  # noqa: F401

        pkg_file = Path(vico.__file__).resolve()
        project_root = pkg_file.parent.parent.parent  # src/vico/__init__.py → project root
        if (project_root / ".vicorc.json").exists():
            return project_root
    except Exception:
        pass

    return current


@lru_cache(maxsize=1)
def _get_config_root() -> Path:
    """Return the config root, loading .env on first call (cached)."""
    root = _find_config_root()
    load_dotenv(dotenv_path=root / ".env")
    return root


def _load_vicorc(root: Path) -> dict[str, Any]:
    rc_path = root / ".vicorc.json"
    if rc_path.exists():
        try:
            return dict(json.loads(rc_path.read_text()))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"Invalid JSON in '{rc_path}': {exc}") from exc
        except OSError as exc:
            raise ConfigError(f"Cannot read config '{rc_path}': {exc}") from exc
    return {}


# ─── Public API ───────────────────────────────────────────────────────────────


def load_config(cwd: str | None = None) -> AgentConfig:
    """Load and build the full agent configuration."""
    if cwd:
        config_root = _find_config_root(cwd)
        load_dotenv(dotenv_path=config_root / ".env")
    else:
        config_root = _get_config_root()
    working_dir = str(Path(cwd).resolve()) if cwd else os.getcwd()
    rc = _load_vicorc(config_root)

    llm = _parse_llm_config(rc)
    context = _parse_context_config(rc)
    tools = _parse_tools_config(rc)
    limits = _parse_limits_config(rc)

    return AgentConfig(llm=llm, context=context, tools=tools, limits=limits, cwd=working_dir)


def lookup_provider(provider_name: str) -> dict[str, str]:
    """Look up a provider's config at runtime (used by /model command)."""
    from vico.llm.models import PROVIDER_DEFAULTS

    rc = _load_vicorc(_get_config_root())
    providers = rc.get("providers", {})

    provider = providers.get(provider_name.lower())
    if not provider:
        raise ProviderUnknownError(
            f"Unknown provider '{provider_name}'. "
            f"Supported: {', '.join(providers.keys()) if providers else 'none'}"
        )

    api_key_env = provider.get("api_key_env", f"{provider_name.upper()}_API_KEY")
    base_url = provider.get("base_url", "") or PROVIDER_DEFAULTS.get(provider_name.lower(), {}).get("base_url", "")
    api_key = os.environ.get(api_key_env, "")

    return {
        "provider": provider_name.lower(),
        "api_key": api_key,
        "api_key_env": api_key_env,
        "base_url": base_url,
    }


# ─── Internal parsers ────────────────────────────────────────────────────────


def _parse_llm_config(rc: dict[str, Any]) -> LLMConfig:
    llm_section = rc.get("llm", {}).get("default", {})
    providers = rc.get("providers", {})

    provider_name = llm_section.get("provider", "deepseek").lower()

    provider_cfg = providers.get(provider_name, {})
    if not provider_cfg:
        raise ConfigError(
            f"Provider '{provider_name}' not found in .vicorc.json.\n"
            f"Available providers: {', '.join(providers.keys()) if providers else 'none'}"
        )

    api_key_env = provider_cfg.get("api_key_env", f"{provider_name.upper()}_API_KEY")
    api_key = os.environ.get(api_key_env, "")
    if not api_key:
        raise ProviderAuthError(
            f"Missing API key for provider '{provider_name}'.\n"
            f"Set {api_key_env} in your .env file.\n"
            "See .env.example for reference."
        )

    model = llm_section.get("model", provider_cfg.get("default_model", ""))
    model_params: dict[str, Any] = provider_cfg.get("models", {}).get(model, {})

    def _get(key: str, default: Any) -> Any:
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
        max_tokens=_get("max_tokens", _get("max_completion_tokens", DEFAULT_MAX_TOKENS)),
        temperature=_get("temperature", 1.0),
        top_p=_get("top_p", None),
        stop=_get("stop", None),
        thinking_enabled=_get("thinking_enabled", True),
        provider_options={
            k: _get(k, default)
            for k, default in {
                "reasoning_effort": "max",
                "response_format": "text",
            }.items()
        },
    )


def _parse_context_config(rc: dict[str, Any]) -> ContextConfig:
    section = rc.get("context", {})
    return ContextConfig(
        max_tokens=section.get("max_tokens", 1000000),
        reserve_tokens=section.get("reserve_tokens", 131072),
        compression_threshold=section.get("compression_threshold", 0.85),
    )


def _parse_tools_config(rc: dict[str, Any]) -> ToolsConfig:
    section = rc.get("tools", {})
    return ToolsConfig(
        auto_approve=section.get("auto_approve", ["low"]),
        timeout_ms=section.get("timeout_ms", 30000),
        env_whitelist=section.get("env_whitelist", []),
    )


def _parse_limits_config(rc: dict[str, Any]) -> AgentLimits:
    section = rc.get("limits", {})
    return AgentLimits(
        max_iterations=section.get("max_iterations", 30),
    )
