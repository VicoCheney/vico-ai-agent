"""Agent configuration and statistics types."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from vico.tools.types.execution import ToolRiskLevel

# Default max tokens — shared constant used by both config loader and LLM factory
DEFAULT_MAX_TOKENS = 131072


@dataclass
class LLMConfig:
    provider: str = "mimo"
    api_key: str = ""
    base_url: str = "https://token-plan-sgp.xiaomimimo.com/v1"
    model: str = "mimo-v2.5-pro"
    max_tokens: int = DEFAULT_MAX_TOKENS
    temperature: float = 1.0
    top_p: float | None = None
    stop: list[str] | None = None
    thinking_enabled: bool = True
    # Provider-specific parameters (e.g. reasoning_effort for DeepSeek,
    # response_format for any provider that supports it).
    provider_options: dict = field(default_factory=dict)  # type: ignore[type-arg]


@dataclass
class ContextConfig:
    max_tokens: int = 60000
    reserve_tokens: int = 4096
    compression_threshold: float = 0.85


@dataclass
class ToolsConfig:
    auto_approve: list[ToolRiskLevel] = field(default_factory=lambda: ["low"])
    timeout_ms: int = 30000
    # When non-empty, only listed variables (and VICO_* prefixed ones) are passed to child processes.
    # When empty (default), the full os.environ minus secret-pattern vars is passed.
    env_whitelist: list[str] = field(default_factory=list)


@dataclass
class AgentLimits:
    """Configurable safety limits for the agent loop."""

    max_iterations: int = 30  # Maximum tool-use iterations per user message


@dataclass
class AgentConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    limits: AgentLimits = field(default_factory=AgentLimits)
    cwd: str = field(default_factory=os.getcwd)


@dataclass
class ContextStats:
    message_count: int
    estimated_tokens: int
    max_tokens: int
    usage_percent: float
    is_near_limit: bool
