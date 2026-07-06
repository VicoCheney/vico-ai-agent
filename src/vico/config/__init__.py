"""
vico.config — configuration loading and types.

Public API (backward-compatible with the old vico.config module):
  load_config(cwd=None)   → AgentConfig
  load_llm_config(...)    → LLMConfig
  lookup_provider(name)   → dict[str, str]
  DEFAULT_MAX_TOKENS      → int

Types live in config/types/config.py and are re-exported here for convenience.
"""

from __future__ import annotations

from vico.config.loader import load_config, load_llm_config, lookup_provider
from vico.config.types.config import (
    DEFAULT_MAX_TOKENS,
    AgentConfig,
    AgentLimits,
    ContextConfig,
    ContextStats,
    LLMConfig,
    ToolsConfig,
)

__all__ = [
    # loader
    "load_config",
    "load_llm_config",
    "lookup_provider",
    # types
    "AgentConfig",
    "AgentLimits",
    "ContextConfig",
    "ContextStats",
    "DEFAULT_MAX_TOKENS",
    "LLMConfig",
    "ToolsConfig",
]
