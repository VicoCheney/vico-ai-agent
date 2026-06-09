"""\nAgent Loop — The core "think → act → observe" engine\n\nThe Executor LLM decides for itself whether a task needs upfront planning.\nWhen it determines planning is warranted it emits a <plan> block before\ncalling any tools, which lets it batch all independent calls in subsequent\nturns and reduce LLM round-trips from O(N) to closer to O(log N).\n"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from vico.core.context_manager import ContextManager
from vico.core.permission_controller import PermissionController
from vico.core.system_prompt import build_system_prompt
from vico.core.types import (
    LLM,
    AgentConfig,
    AgentState,
    DoneChunk,
    ErrorChunk,
    LLMRequest,
    ReasoningChunk,
    TextChunk,
    ToolCall,
    ToolCallChunk,
    ToolExecutionContext,
    ToolResult,
)
from vico.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from vico.core.context_manager import ContextStats

logger = logging.getLogger(__name__)

# Approval label constants — shared between _dispatch_tool_calls and _execute_one
# to guarantee consistent values without risking silent string mismatch.
_APPROVAL_AUTO = "auto approved"
_APPROVAL_ONCE = "approved"
_APPROVAL_ALWAYS = "approved always"

OnThinkingCallback = Callable[[str], None]
OnTextCallback = Callable[[str], None]
OnToolCallCallback = Callable[[ToolCall], None]
OnToolResultCallback = Callable[[ToolCall, ToolResult], None]
OnErrorCallback = Callable[[Exception], None]
OnDoneCallback = Callable[[int, int], None]  # prompt_tokens, completion_tokens
OnLoopCallback = Callable[[int], None]
ApprovalCallback = Callable[[ToolCall], Coroutine[Any, Any, Literal["approve", "approve_always", "deny"]]]


@dataclass
class AgentCallbacks:
    """All event callbacks from the agent loop to the UI."""

    on_thinking: OnThinkingCallback | None = None
    on_text: OnTextCallback | None = None
    on_tool_call: OnToolCallCallback | None = None
    on_tool_result: OnToolResultCallback | None = None
    on_error: OnErrorCallback | None = None
    on_done: OnDoneCallback | None = None
    on_loop: OnLoopCallback | None = None
    request_approval: ApprovalCallback | None = None


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
    ) -> None:
        self._llm = llm
        self._context = context
        self._tool_registry = tool_registry
        self._permissions = permissions
        self._config = config
        self._callbacks = callbacks
        self._system_prompt = build_system_prompt(config.cwd)
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
        """The cancellation event for this agent run.

        Expose as a public property so callers (e.g. the approval callback
        in cli/__init__.py) don't need to reach into the private _cancel_event
        attribute, preserving encapsulation for future refactors.
        """
        return self._cancel_event

    def cancel(self) -> None:
        """Request cancellation of the current run."""
        self._cancel_event.set()

    def switch_model(self, llm: LLM) -> None:
        """Hot-swap the LLM provider at runtime."""
        self._llm = llm

    # ─── Main Run ─────────────────────────────────────────────────────────────

    async def run(self, user_input: str, max_iterations: int = 30) -> None:
        """Run the agent loop for one user message."""
        if self._state == "running":
            raise RuntimeError("Agent is already running.")

        self._state = "running"
        self._cancel_event.clear()

        self._context.add_user_message(user_input)
        self._context.maybe_compress(self._system_prompt)

        try:
            await self._loop(max_iterations)
        except asyncio.CancelledError:
            logger.debug("Agent run cancelled (user requested stop).")
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

            # ── Step 1: Call LLM ─────────────────────────────────────────
            accumulated_text, pending_tool_calls = await self._stream_llm()

            # ── Step 2: Save assistant response to context ───────────────
            self._context.add_assistant_message(
                text=accumulated_text,
                tool_calls=[{"id": tc.id, "name": tc.name, "input": tc.input} for tc in pending_tool_calls],
            )

            # ── Step 3: If no tool calls, we're done ─────────────────────
            if not pending_tool_calls:
                break

            # ── Step 4: Execute tool calls ────────────────────────────────
            tool_results = await self._dispatch_tool_calls(pending_tool_calls)

            for tool_call, result in tool_results:
                self._context.add_tool_result(
                    tool_use_id=tool_call.id,
                    tool_name=tool_call.name,
                    content=result.output if result.success else (result.error or "Unknown error"),
                    is_error=not result.success,
                )

            self._context.maybe_compress(self._system_prompt)

    async def _stream_llm(self) -> tuple[str, list[ToolCall]]:
        """Call the LLM and collect the full response.

        Streams text/reasoning/tool-call chunks, fires the corresponding
        callbacks, and returns ``(accumulated_text, pending_tool_calls)``
        once the stream is exhausted or cancelled.
        """
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
                raise chunk.error

        return accumulated_text, pending_tool_calls

    async def _dispatch_tool_calls(
        self,
        pending_tool_calls: list[ToolCall],
    ) -> list[tuple[ToolCall, ToolResult]]:
        """Execute a batch of tool calls and return ``(ToolCall, ToolResult)`` pairs.

        If any tool requires human approval the calls are run **sequentially**
        so the approval dialog gets a clean terminal without concurrent output.
        Otherwise all tools run **concurrently** via ``asyncio.gather`` for speed.

        ``return_exceptions=True`` is used in the concurrent path so that an
        unexpected exception in one tool does not cancel the others.  Such
        exceptions are wrapped as failed ``ToolResult``s and fed back to the LLM
        so the agent can recover gracefully instead of aborting the whole turn.
        """
        needs_approval = any(
            not self._permissions.is_auto_approved(tc, self._tool_registry)
            for tc in pending_tool_calls
        )

        cb_tc = self._callbacks.on_tool_call

        if needs_approval:
            # Sequential path — preserves terminal cleanliness for approval dialogs.
            # Each tool is notified then immediately executed (interleaved) so the
            # spinner for one tool resolves before the approval box for the next one.
            tool_results: list[tuple[ToolCall, ToolResult]] = []
            for tc in pending_tool_calls:
                if cb_tc:
                    cb_tc(tc)
                result_pair = await self._execute_one(tc)
                tool_results.append(result_pair)
            return tool_results

        # Concurrent path — notify all tools first, then run them in parallel.
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
                        decision: Literal["approve", "approve_always", "deny"] = await approval_fn(tool_call)
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
            env=dict(os.environ),
            cancel_event=self._cancel_event,
        )
        result = await self._tool_registry.execute(tool_call.name, tool_call.input, exec_ctx)
        result.metadata["approval"] = approval_label

        cb_tr = self._callbacks.on_tool_result
        if cb_tr:
            cb_tr(tool_call, result)

        return tool_call, result

    def get_context_stats(self, system_prompt: str = "") -> ContextStats:
        """Return current context token usage statistics.

        Exposes context stats as a public API so callers do not need to reach
        into the private ``_context`` attribute, preserving encapsulation.
        """
        return self._context.get_stats(system_prompt)

    def reset(self) -> None:
        """Reset context for a new conversation."""
        self._context.clear()
        self._state = "idle"
