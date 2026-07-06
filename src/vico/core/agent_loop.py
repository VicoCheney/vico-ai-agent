"""Agent Loop — think → act → observe engine.

Skill activation primarily flows through the structured ``activate_skill`` tool.
The legacy ``<use_skill>SKILL_ID</use_skill>`` text tag remains supported for
backward compatibility.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from vico.config.types.config import AgentConfig
from vico.core.context_manager import ContextManager
from vico.core.permission_controller import PermissionController
from vico.core.skill_provider import ISkillProvider
from vico.core.skill_runtime import SkillRuntime
from vico.core.system_prompt import build_system_prompt
from vico.core.tool_executor import ToolExecutor
from vico.core.types import (
    AgentCallbacks,
    AgentState,
)
from vico.llm.base import LLM
from vico.llm.types.request import LLMRequest
from vico.llm.types.stream import (
    DoneChunk,
    ErrorChunk,
    ReasoningChunk,
    TextChunk,
    ToolCallChunk,
)
from vico.tools.registry import ToolRegistry
from vico.tools.types.call import ToolCall

__all__ = ["AgentCallbacks", "AgentLoop"]

if TYPE_CHECKING:
    from vico.config.types.config import ContextStats

logger = logging.getLogger(__name__)


class AgentLoop:
    """The core agent loop: think → act → observe."""

    def __init__(
        self,
        llm: LLM,
        context: ContextManager,
        tool_registry: ToolRegistry,
        permissions: PermissionController,
        config: AgentConfig,
        callbacks: AgentCallbacks,
        skill_loader: ISkillProvider | None = None,
    ) -> None:
        self._llm = llm
        self._context = context
        self._tool_registry = tool_registry
        self._permissions = permissions
        self._config = config
        self._callbacks = callbacks
        self._skill_loader: ISkillProvider | None = skill_loader
        self._system_prompt = build_system_prompt(config.cwd, skill_loader=skill_loader)
        self._state: AgentState = "idle"
        self._cancel_event = asyncio.Event()
        self._skill_runtime = SkillRuntime(
            skill_loader=skill_loader,
            context=context,
            cwd=config.cwd,
            on_skill_activated=callbacks.on_skill_activated,
        )
        self._tool_executor = ToolExecutor(
            registry=tool_registry,
            permissions=permissions,
            config=config,
            callbacks=callbacks,
            cancel_event=self._cancel_event,
            set_state=self._set_state,
        )

    # ─── Public API ───────────────────────────────────────────────────────────

    @property
    def llm(self) -> LLM:
        return self._llm

    @property
    def config(self) -> AgentConfig:
        return self._config

    @property
    def state(self) -> AgentState:
        return self._state

    @property
    def cancel_event(self) -> asyncio.Event:
        return self._cancel_event

    def cancel(self) -> None:
        """Request cancellation of the current run."""
        self._cancel_event.set()

    def _set_state(self, state: AgentState) -> None:
        self._state = state

    def switch_model(self, llm: LLM) -> None:
        """Hot-swap the LLM provider at runtime."""
        self._llm = llm

    async def aclose(self) -> None:
        """Close the LLM HTTP client."""
        await self._llm.aclose()

    # ─── Main Run ─────────────────────────────────────────────────────────────

    async def run(self, user_input: str, max_iterations: int | None = None) -> None:
        """Run the agent loop for one user message."""
        if self._state == "running":
            raise RuntimeError("Agent is already running.")

        effective_max = max_iterations or self._config.limits.max_iterations

        self._state = "running"
        self._cancel_event.clear()

        self._context.add_user_message(user_input)
        self._context.maybe_compress(self._system_prompt)

        try:
            await self._loop(effective_max)
        except asyncio.CancelledError:
            logger.debug("Agent run cancelled.")
        except Exception as exc:
            cb = self._callbacks.on_error
            if cb:
                cb(exc)
        finally:
            self._state = "idle"

    async def _loop(self, max_iterations: int) -> None:
        for iteration in range(max_iterations):
            if self._cancel_event.is_set():
                break

            cb_loop = self._callbacks.on_loop
            if cb_loop:
                cb_loop(iteration)

            accumulated_text, pending_tool_calls = await self._stream_llm()

            self._context.add_assistant_message(
                text=accumulated_text,
                tool_calls=[{"id": tc.id, "name": tc.name, "input": tc.input} for tc in pending_tool_calls],
            )

            if self._skill_runtime.enabled and not pending_tool_calls:
                skill_injected = self._skill_runtime.inject_from_legacy_tag(accumulated_text)
                if skill_injected:
                    continue

            if not pending_tool_calls:
                break

            tool_results = await self._tool_executor.execute_batch(pending_tool_calls)

            for tool_call, result in tool_results:
                self._context.add_tool_result(
                    tool_use_id=tool_call.id,
                    tool_name=tool_call.name,
                    content=result.output if result.success else (result.error or "Unknown error"),
                    is_error=not result.success,
                )
                self._skill_runtime.inject_from_tool_result(result)

            self._context.maybe_compress(self._system_prompt)

    # ─── Skill Injection ──────────────────────────────────────────────────────

    def inject_skill_by_id(self, skill_id: str, arguments: str = "") -> bool:
        """Inject a skill by ID (user-triggered via /skill command).

        Bypasses disable_model_invocation — user explicitly requested it.
        Returns True if the skill was found and injected.
        """
        return self._skill_runtime.inject_by_id(
            skill_id,
            arguments=arguments,
            bypass_manual_only=True,
        )

    # ─── LLM Streaming ────────────────────────────────────────────────────────

    async def _stream_llm(self) -> tuple[str, list[ToolCall]]:
        """Call the LLM and collect the full response."""
        messages = self._context.get_messages()
        tool_defs = self._tool_registry.get_definitions() if self._llm.supports_tool_use() else None

        accumulated_text = ""
        pending_tool_calls: list[ToolCall] = []

        stream = self._llm.stream(
            LLMRequest(
                system=self._system_prompt,
                messages=messages,
                tools=tool_defs,
                max_tokens=self._config.llm.max_tokens,
                temperature=self._config.llm.temperature,
            )
        )

        async for chunk in stream:
            if self._cancel_event.is_set():
                break

            if isinstance(chunk, TextChunk):
                accumulated_text += chunk.content
                cb = self._callbacks.on_text
                if cb:
                    cb(chunk.content)

            elif isinstance(chunk, ReasoningChunk):
                cb = self._callbacks.on_thinking
                if cb:
                    cb(chunk.content)

            elif isinstance(chunk, ToolCallChunk):
                pending_tool_calls.append(chunk.tool_call)

            elif isinstance(chunk, DoneChunk):
                pt = chunk.usage.prompt_tokens if chunk.usage else 0
                ct = chunk.usage.completion_tokens if chunk.usage else 0
                if chunk.usage:
                    self._context.update_last_usage(chunk.usage)
                cb_done = self._callbacks.on_done
                if cb_done:
                    cb_done(pt, ct)

            elif isinstance(chunk, ErrorChunk):
                logger.error("LLM stream error: %s", chunk.error)
                raise chunk.error

        return accumulated_text, pending_tool_calls

    def get_context_stats(self, system_prompt: str | None = None) -> ContextStats:
        """Return current context token usage statistics."""
        return self._context.get_stats(self._system_prompt if system_prompt is None else system_prompt)

    def debug_snapshot(self) -> dict[str, object]:
        """Return compact runtime diagnostics for /debug commands."""
        return {
            "state": self._state,
            "model": {
                "provider": self._config.llm.provider,
                "model": self._config.llm.model,
            },
            "context": self._context.get_stats(self._system_prompt),
            "messages": self._context.debug_messages(),
            "tools": self._tool_registry.describe_tools(),
        }

    def reset(self) -> None:
        """Reset context for a new conversation."""
        self._context.clear()
        self._state = "idle"
