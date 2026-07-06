"""Tool execution orchestration: approval, environment, execution, and callbacks."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from typing import Literal

from vico.config.types.config import AgentConfig
from vico.core.permission_controller import PermissionController
from vico.core.types import AgentCallbacks, AgentState
from vico.tools.registry import ToolRegistry
from vico.tools.types.call import ToolCall
from vico.tools.types.execution import ApprovalLabel, ToolExecutionContext, ToolResult

logger = logging.getLogger(__name__)

_APPROVAL_AUTO: ApprovalLabel = "auto approved"
_APPROVAL_ONCE: ApprovalLabel = "approved"
_APPROVAL_ALWAYS: ApprovalLabel = "approved always"


class ToolExecutor:
    """Executes tool calls with permission and UI callback handling."""

    def __init__(
        self,
        registry: ToolRegistry,
        permissions: PermissionController,
        config: AgentConfig,
        callbacks: AgentCallbacks,
        cancel_event: asyncio.Event,
        set_state: Callable[[AgentState], None],
    ) -> None:
        self._registry = registry
        self._permissions = permissions
        self._config = config
        self._callbacks = callbacks
        self._cancel_event = cancel_event
        self._set_state = set_state
        self._approval_lock = asyncio.Lock()

    async def execute_batch(
        self,
        pending_tool_calls: list[ToolCall],
    ) -> list[tuple[ToolCall, ToolResult]]:
        """Execute a batch of tool calls.

        Tools requiring approval run sequentially for a clean permission dialog.
        Auto-approved tools run concurrently.
        """
        needs_approval = any(
            not self._permissions.is_auto_approved(tc, self._registry)
            for tc in pending_tool_calls
        )

        cb_tc = self._callbacks.on_tool_call

        if needs_approval:
            tool_results: list[tuple[ToolCall, ToolResult]] = []
            for tc in pending_tool_calls:
                if cb_tc:
                    cb_tc(tc)
                result_pair = await self.execute_one(tc)
                tool_results.append(result_pair)
            return tool_results

        if self._has_mutating_call(pending_tool_calls):
            tool_results = []
            for tc in pending_tool_calls:
                if cb_tc:
                    cb_tc(tc)
                result_pair = await self.execute_one(tc)
                tool_results.append(result_pair)
            return tool_results

        for tc in pending_tool_calls:
            if cb_tc:
                cb_tc(tc)

        raw_results = await asyncio.gather(
            *[self.execute_one(tc) for tc in pending_tool_calls],
            return_exceptions=True,
        )

        tool_results = []
        for tc, outcome in zip(pending_tool_calls, raw_results):
            if isinstance(outcome, BaseException):
                err_result = ToolResult(
                    success=False,
                    output="",
                    error=f"Unexpected error: {outcome}",
                )
                err_result.set_approval(_APPROVAL_AUTO)
                cb_tr = self._callbacks.on_tool_result
                if cb_tr:
                    cb_tr(tc, err_result)
                tool_results.append((tc, err_result))
            else:
                tool_results.append(outcome)
        return tool_results

    async def execute_one(self, tool_call: ToolCall) -> tuple[ToolCall, ToolResult]:
        """Permission check -> execute -> notify UI for a single tool call."""
        if self._cancel_event.is_set():
            return tool_call, ToolResult(success=False, output="", error="Cancelled")

        auto_approved = self._permissions.is_auto_approved(tool_call, self._registry)
        approval_label: ApprovalLabel = _APPROVAL_AUTO

        if not auto_approved:
            async with self._approval_lock:
                if not self._permissions.is_auto_approved(tool_call, self._registry):
                    self._set_state("waiting_approval")
                    approval_fn = self._callbacks.request_approval
                    if approval_fn:
                        logger.info("Tool %r requires approval", tool_call.name)
                        decision: Literal["approve", "approve_always", "deny"] = await approval_fn(tool_call)
                        logger.info("Tool %r decision: %s", tool_call.name, decision)
                    else:
                        decision = "deny"
                    self._set_state("running")

                    if decision == "approve_always":
                        self._permissions.grant_session_approval(tool_call)
                        approval_label = _APPROVAL_ALWAYS
                    elif decision == "deny":
                        denied = ToolResult(
                            success=False,
                            output="",
                            error="Tool execution denied by user.",
                        )
                        denied.set_approval("denied")
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
            timeout_ms=self._config.tools.timeout_ms,
        )
        logger.info("Executing tool %r (approval=%s)", tool_call.name, approval_label)
        result = await self._registry.execute(tool_call.name, tool_call.input, exec_ctx)
        result.set_approval(approval_label)
        if not result.success:
            logger.warning("Tool %r failed: %s", tool_call.name, result.error)

        cb_tr = self._callbacks.on_tool_result
        if cb_tr:
            cb_tr(tool_call, result)

        return tool_call, result

    # Env var names matching these patterns are never forwarded to child processes.
    _SECRET_PATTERNS: tuple[str, ...] = (
        "_API_KEY", "_SECRET", "_TOKEN", "_PASSWORD", "_CREDENTIAL",
        "_AUTH", "_PRIVATE_KEY", "AWS_SECRET", "AWS_ACCESS",
    )

    def _build_tool_env(self) -> dict[str, str]:
        """Build the env dict passed to tool child processes."""
        whitelist = self._config.tools.env_whitelist
        safe_defaults = {"PATH", "HOME", "SHELL", "LANG", "TERM", "USER", "TMPDIR", "XDG_CONFIG_HOME"}

        result: dict[str, str] = {}
        for key, value in os.environ.items():
            if self._is_secret_key(key):
                continue
            if whitelist:
                allowed = safe_defaults | set(whitelist)
                if key in allowed or key.startswith("VICO_"):
                    result[key] = value
            else:
                result[key] = value
        return result

    @classmethod
    def _is_secret_key(cls, key: str) -> bool:
        upper = key.upper()
        return any(pat in upper for pat in cls._SECRET_PATTERNS)

    @staticmethod
    def _has_mutating_call(tool_calls: list[ToolCall]) -> bool:
        return any(tc.name in {"write", "edit"} for tc in tool_calls)
