"""Agent Loop — think → act → observe engine.

Skill activation: when the LLM emits ``<use_skill>SKILL_ID</use_skill>`` in its
response, _loop() loads the skill body, injects it as a user message, and
continues to the next LLM turn.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import TYPE_CHECKING, Literal

from vico.config.types.config import AgentConfig
from vico.core.context_manager import ContextManager
from vico.core.permission_controller import PermissionController
from vico.core.skill_provider import ISkillProvider
from vico.core.system_prompt import build_system_prompt
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
from vico.tools.types.execution import ToolExecutionContext, ToolResult

__all__ = ["AgentCallbacks", "AgentLoop"]

if TYPE_CHECKING:
    from vico.config.types.config import ContextStats

logger = logging.getLogger(__name__)

_APPROVAL_AUTO = "auto approved"
_APPROVAL_ONCE = "approved"
_APPROVAL_ALWAYS = "approved always"

_USE_SKILL_RE = re.compile(r"<use_skill>\s*([^<\s]+)\s*</use_skill>", re.IGNORECASE)


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
        self._approval_lock = asyncio.Lock()

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

            if self._skill_loader and not pending_tool_calls:
                skill_injected = self._maybe_inject_skill(accumulated_text)
                if skill_injected:
                    continue

            if not pending_tool_calls:
                break

            tool_results = await self._dispatch_tool_calls(pending_tool_calls)

            for tool_call, result in tool_results:
                self._context.add_tool_result(
                    tool_use_id=tool_call.id,
                    tool_name=tool_call.name,
                    content=result.output if result.success else (result.error or "Unknown error"),
                    is_error=not result.success,
                )

            self._context.maybe_compress(self._system_prompt)

    # ─── Skill Injection ──────────────────────────────────────────────────────

    def _maybe_inject_skill(self, text: str) -> bool:
        """Detect <use_skill>ID</use_skill> tag and inject the skill body.

        Returns True if a skill was injected (or the tag was consumed).
        Only the first match per turn is processed.
        """
        match = _USE_SKILL_RE.search(text)
        if not match:
            return False

        skill_id = match.group(1).strip()
        if self._skill_loader is None:
            return False

        content = self._skill_loader.get_skill_content(skill_id)
        if not content:
            logger.warning("Skill %r requested but not found.", skill_id)
            self._context.add_user_message(
                f"[System] Skill '{skill_id}' was not found. Please proceed without it."
            )
            return True

        if content.meta.disable_model_invocation:
            logger.info("Skill %r has disable_model_invocation=True.", skill_id)
            self._context.add_user_message(
                f"[System] Skill '{skill_id}' can only be activated by the user "
                f"via `/skill {skill_id}`. Please proceed without it."
            )
            return True

        self._context.add_user_message(
            f"[Skill: {content.meta.name}]\n\n{content.body}"
        )
        logger.info("Injected skill %r (%d chars).", skill_id, len(content.body))

        cb = self._callbacks.on_skill_activated
        if cb:
            cb(content.meta)

        return True

    def inject_skill_by_id(self, skill_id: str) -> bool:
        """Inject a skill by ID (user-triggered via /skill command).

        Bypasses disable_model_invocation — user explicitly requested it.
        Returns True if the skill was found and injected.
        """
        if not self._skill_loader:
            return False

        content = self._skill_loader.get_skill_content(skill_id)
        if not content:
            return False

        self._context.add_user_message(
            f"[Skill: {content.meta.name}]\n\n{content.body}"
        )
        logger.info("User-injected skill %r (%d chars).", skill_id, len(content.body))

        cb = self._callbacks.on_skill_activated
        if cb:
            cb(content.meta)

        return True

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

    async def _dispatch_tool_calls(
        self,
        pending_tool_calls: list[ToolCall],
    ) -> list[tuple[ToolCall, ToolResult]]:
        """Execute a batch of tool calls.

        Tools requiring approval run sequentially (clean terminal for dialog).
        Auto-approved tools run concurrently via asyncio.gather.
        """
        needs_approval = any(
            not self._permissions.is_auto_approved(tc, self._tool_registry)
            for tc in pending_tool_calls
        )

        cb_tc = self._callbacks.on_tool_call

        if needs_approval:
            tool_results: list[tuple[ToolCall, ToolResult]] = []
            for tc in pending_tool_calls:
                if cb_tc:
                    cb_tc(tc)
                result_pair = await self._execute_one(tc)
                tool_results.append(result_pair)
            return tool_results

        for tc in pending_tool_calls:
            if cb_tc:
                cb_tc(tc)
        raw_results = await asyncio.gather(
            *[self._execute_one(tc) for tc in pending_tool_calls],
            return_exceptions=True,
        )
        tool_results = []
        for tc, outcome in zip(pending_tool_calls, raw_results):
            if isinstance(outcome, BaseException):
                err_result = ToolResult(
                    success=False,
                    output="",
                    error=f"Unexpected error: {outcome}",
                    metadata={"approval": _APPROVAL_AUTO},
                )
                cb_tr = self._callbacks.on_tool_result
                if cb_tr:
                    cb_tr(tc, err_result)
                tool_results.append((tc, err_result))
            else:
                tool_results.append(outcome)
        return tool_results

    # Env var names matching these patterns are never forwarded to child processes.
    _SECRET_PATTERNS: tuple[str, ...] = (
        "_API_KEY", "_SECRET", "_TOKEN", "_PASSWORD", "_CREDENTIAL",
        "_AUTH", "_PRIVATE_KEY", "AWS_SECRET", "AWS_ACCESS",
    )

    def _build_tool_env(self) -> dict[str, str]:
        """Build the env dict passed to tool child processes.

        Whitelist mode (env_whitelist non-empty): only listed vars + VICO_* + safe defaults.
        Default mode (empty whitelist): full os.environ minus secret-pattern vars.
        """
        whitelist = self._config.tools.env_whitelist
        safe_defaults = {"PATH", "HOME", "SHELL", "LANG", "TERM", "USER", "TMPDIR", "XDG_CONFIG_HOME"}

        result: dict[str, str] = {}
        for key, value in os.environ.items():
            if whitelist:
                allowed = safe_defaults | set(whitelist)
                if key in allowed or key.startswith("VICO_"):
                    result[key] = value
            else:
                if not self._is_secret_key(key):
                    result[key] = value
        return result

    @classmethod
    def _is_secret_key(cls, key: str) -> bool:
        """Return True if the env var name matches a secret/credential pattern."""
        upper = key.upper()
        return any(pat in upper for pat in cls._SECRET_PATTERNS)

    async def _execute_one(self, tool_call: ToolCall) -> tuple[ToolCall, ToolResult]:
        """Permission check → execute → notify UI for a single tool call."""
        if self._cancel_event.is_set():
            return tool_call, ToolResult(success=False, output="", error="Cancelled")

        auto_approved = self._permissions.is_auto_approved(tool_call, self._tool_registry)
        approval_label: str = _APPROVAL_AUTO

        if not auto_approved:
            async with self._approval_lock:
                if not self._permissions.is_auto_approved(tool_call, self._tool_registry):
                    self._state = "waiting_approval"
                    approval_fn = self._callbacks.request_approval
                    if approval_fn:
                        logger.info("Tool %r requires approval", tool_call.name)
                        decision: Literal["approve", "approve_always", "deny"] = await approval_fn(tool_call)
                        logger.info("Tool %r decision: %s", tool_call.name, decision)
                    else:
                        decision = "deny"
                    self._state = "running"

                    if decision == "approve_always":
                        self._permissions.grant_session_approval(tool_call.name)
                        approval_label = _APPROVAL_ALWAYS
                    elif decision == "deny":
                        denied = ToolResult(
                            success=False,
                            output="",
                            error="Tool execution denied by user.",
                            metadata={"approval": "denied"},
                        )
                        cb_tr = self._callbacks.on_tool_result
                        if cb_tr:
                            cb_tr(tool_call, denied)
                        return tool_call, denied
                    else:
                        approval_label = _APPROVAL_ONCE
                else:
                    approval_label = _APPROVAL_AUTO

        exec_ctx = ToolExecutionContext(
            cwd=self._config.cwd,
            env=self._build_tool_env(),
            cancel_event=self._cancel_event,
        )
        logger.info("Executing tool %r (approval=%s)", tool_call.name, approval_label)
        result = await self._tool_registry.execute(tool_call.name, tool_call.input, exec_ctx)
        result.metadata["approval"] = approval_label
        if not result.success:
            logger.warning("Tool %r failed: %s", tool_call.name, result.error)

        cb_tr = self._callbacks.on_tool_result
        if cb_tr:
            cb_tr(tool_call, result)

        return tool_call, result

    def get_context_stats(self, system_prompt: str = "") -> ContextStats:
        """Return current context token usage statistics."""
        return self._context.get_stats(system_prompt)

    def reset(self) -> None:
        """Reset context for a new conversation."""
        self._context.clear()
        self._state = "idle"
