"""
Agent Loop — The core "think → act → observe" engine

Execution model
───────────────
For complex or multi-step tasks an *upfront planning phase* is inserted
before the first tool-execution turn.  The planner sees the user's message
and the available tools, but receives NO tool definitions — it can only
produce a structured <plan> block (plain text).  The plan is injected into
the conversation as a system note so the executor can immediately batch
all independent tool calls in subsequent turns.

This reduces the number of LLM round-trips for long tasks from O(N) to
closer to O(log N) by encouraging the model to parallelise independent work.
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Literal

from vico.core.context_manager import ContextManager
from vico.core.permission_controller import PermissionController
from vico.core.system_prompt import build_planner_prompt, build_system_prompt
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
    ToolResult,
)
from vico.tools.registry import ToolRegistry

OnThinkingCallback = Callable[[str], None]
OnTextCallback = Callable[[str], None]
OnToolCallCallback = Callable[[ToolCall], None]
OnToolResultCallback = Callable[[ToolCall, ToolResult], None]
OnErrorCallback = Callable[[Exception], None]
OnDoneCallback = Callable[[int, int], None]  # prompt_tokens, completion_tokens
OnLoopCallback = Callable[[int], None]
OnPlanCallback = Callable[[str], None]  # plan text produced by the planner
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
    on_plan: OnPlanCallback | None = None
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
        self._planner_prompt = build_planner_prompt(config.cwd)
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

    def cancel(self) -> None:
        """Request cancellation of the current run."""
        self._cancel_event.set()

    def switch_model(self, llm: LLM) -> None:
        """Hot-swap the LLM provider at runtime."""
        self._llm = llm

    # ─── Complexity Heuristic ─────────────────────────────────────────────────

    def _is_complex_task(self, user_input: str) -> bool:
        """Return True when the task likely needs an upfront plan.

        Heuristics (any one is sufficient):
        1. Word count ≥ min_words AND input looks like a real instruction
           (not just "ls" or "hello")
        2. At least one complexity keyword is found in the text
        """
        cfg = self._config.planning
        if not cfg.enabled:
            return False

        text_lower = user_input.lower()

        # Keyword match — fast path
        for kw in cfg.complexity_keywords:
            if kw.lower() in text_lower:
                return True

        # Length heuristic — strip punctuation for a fair word count
        words = re.findall(r"\w+", user_input)
        return len(words) >= cfg.min_words

    # ─── Planning Phase ───────────────────────────────────────────────────────

    async def _run_planning_phase(self, user_input: str) -> str | None:
        """Call the LLM once with a planning-only prompt; return the plan text.

        The planner receives NO tool definitions so it cannot call any tools —
        it can only produce a structured <plan> block.  This gives the executor
        a roadmap that it can follow to batch independent calls.
        """
        if self._cancel_event.is_set():
            return None

        plan_text = ""
        stream = self._llm.stream(
            LLMRequest(
                system=self._planner_prompt,
                messages=self._context.get_messages(),
                tools=None,  # ← no tools — planner can only write text
                max_tokens=2048,
                temperature=self._config.llm.temperature,
            )
        )

        async for chunk in stream:
            if self._cancel_event.is_set():
                break
            if isinstance(chunk, TextChunk):
                plan_text += chunk.content
            elif isinstance(chunk, ReasoningChunk):
                cb = self._callbacks.on_thinking
                if cb:
                    cb(chunk.content)
            elif isinstance(chunk, ErrorChunk):
                # Planning failure is non-fatal — executor continues without a plan
                return None

        plan_text = plan_text.strip()
        if not plan_text:
            return None

        # Fire the on_plan callback so the renderer can display the plan
        cb_plan = self._callbacks.on_plan
        if cb_plan:
            cb_plan(plan_text)

        return plan_text

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
            # ── Optional upfront planning phase ──────────────────────────────
            plan_note: str | None = None
            if self._is_complex_task(user_input):
                plan_note = await self._run_planning_phase(user_input)

            # Inject the plan as a context note so the executor can reference it
            if plan_note:
                self._context.add_assistant_message(
                    text=f"<plan_summary>\n{plan_note}\n</plan_summary>",
                    tool_calls=None,
                )

            await self._loop(max_iterations)
        except asyncio.CancelledError:
            pass
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

            # ── Step 2: Save assistant response to context ───────────────
            self._context.add_assistant_message(
                text=accumulated_text,
                tool_calls=[{"id": tc.id, "name": tc.name, "input": tc.input} for tc in pending_tool_calls],
            )

            # ── Step 3: If no tool calls, we're done ─────────────────────
            if not pending_tool_calls:
                break

            # ── Step 4: Execute tool calls ────────────────────────────────
            #
            # If any tool requires approval, run sequentially so the approval
            # dialog gets a clean terminal without concurrent tool output.
            # Otherwise, run all tools concurrently for speed.

            needs_approval = any(
                not self._permissions.is_auto_approved(tc, self._tool_registry) for tc in pending_tool_calls
            )

            if needs_approval:
                tool_results: list[tuple[ToolCall, ToolResult]] = []
                for tc in pending_tool_calls:
                    cb_tc = self._callbacks.on_tool_call
                    # Always notify the renderer so it can track the tool call.
                    # For auto-approved tools this starts the spinner; for tools
                    # that need approval the renderer records the call so that
                    # collapse_permission_request() and on_tool_result() work
                    # correctly even when they skip the spinner row.
                    if cb_tc:
                        cb_tc(tc)
                    # Execute immediately so the spinner resolves before the
                    # permission box for the next tool appears.
                    result_pair = await self._execute_one(tc)
                    tool_results.append(result_pair)
            else:
                cb_tc = self._callbacks.on_tool_call
                for tc in pending_tool_calls:
                    if cb_tc:
                        cb_tc(tc)
                # codeflicker-fix: EDGE-Issue-007/nbd7ve5rigvbc2siplzv
                # Use return_exceptions=True so that an unexpected exception in one
                # tool does not cancel the other concurrent tools.  Any BaseException
                # results are re-wrapped as failed ToolResults and fed back to the LLM
                # so the agent can recover gracefully instead of aborting the whole turn.
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
                            metadata={"approval": "auto approved"},
                        )
                        cb_tr = self._callbacks.on_tool_result
                        if cb_tr:
                            cb_tr(tc, err_result)
                        tool_results.append((tc, err_result))
                    else:
                        tool_results.append(outcome)

            for tool_call, result in tool_results:
                self._context.add_tool_result(
                    tool_use_id=tool_call.id,
                    tool_name=tool_call.name,
                    content=result.output if result.success else (result.error or "Unknown error"),
                    is_error=not result.success,
                )

            self._context.maybe_compress(self._system_prompt)

    async def _execute_one(self, tool_call: ToolCall) -> tuple[ToolCall, ToolResult]:
        """Permission check → execute → notify UI for a single tool call."""
        from vico.core.types import ToolExecutionContext

        if self._cancel_event.is_set():
            return tool_call, ToolResult(success=False, output="", error="Cancelled")

        auto_approved = self._permissions.is_auto_approved(tool_call, self._tool_registry)
        approval_label: str = "auto approved"

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
                        approval_label = "approved always"
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
                        approval_label = "approved"
                else:
                    approval_label = "auto approved"

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

    def reset(self) -> None:
        """Reset context for a new conversation."""
        self._context.clear()
        self._state = "idle"
