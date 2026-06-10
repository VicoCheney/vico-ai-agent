"""
VicoSession — orchestrates the full agent lifecycle.

Encapsulates object graph assembly (config → components → agent),
callback wiring, and the interactive REPL loop.  ``async_main()``
delegates to ``VicoSession`` so the entry point stays thin and the
assembly logic is independently testable.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Literal

from prompt_toolkit import PromptSession
from rich.console import Console

from vico.cli.renderer import TerminalRenderer
from vico.core.agent_loop import AgentCallbacks, AgentLoop
from vico.core.context_manager import ContextManager
from vico.core.permission_controller import PermissionController
from vico.core.types import AgentConfig, SkillMeta, ToolCall
from vico.llm.llm_factory import create_llm_from_config
from vico.skills.loader import SkillLoader
from vico.tools import BUILTIN_TOOLS
from vico.tools.registry import ToolRegistry

if TYPE_CHECKING:
    pass

console = Console()


class VicoSession:
    """Owns all runtime objects for a single Vico session and runs the REPL."""

    def __init__(self, config: AgentConfig) -> None:
        self._config = config
        self._renderer = TerminalRenderer()
        self._renderer.set_model_label(config.llm.provider, config.llm.model)
        self._renderer.set_cwd(config.cwd)

        # ── Build component graph ────────────────────────────────────────
        self._skill_loader = SkillLoader(cwd=config.cwd)
        self._tool_registry = ToolRegistry()
        self._tool_registry.register_all(BUILTIN_TOOLS)

        self._llm = create_llm_from_config(config)

        self._context = ContextManager(
            max_tokens=config.context.max_tokens,
            reserve_tokens=config.context.reserve_tokens,
            compression_threshold=config.context.compression_threshold,
        )
        self._permissions = PermissionController(auto_approve_risks=config.tools.auto_approve)

        self._renderer.set_permissions_checker(
            lambda tc: self._permissions.is_auto_approved(tc, self._tool_registry)
        )

        quit_event = asyncio.Event()
        self._quit_event = quit_event
        self._session: PromptSession[str] = PromptSession()

        callbacks = self._build_callbacks(quit_event)

        self._agent = AgentLoop(
            llm=self._llm,
            context=self._context,
            tool_registry=self._tool_registry,
            permissions=self._permissions,
            config=config,
            callbacks=callbacks,
            skill_loader=self._skill_loader,
        )

        # Log discovered skills
        if self._skill_loader.get_all_metas():
            skill_names = ", ".join(m.skill_id for m in self._skill_loader.get_all_metas())
            console.print(f"  [dim]Skills loaded: {skill_names}[/dim]")

    # ─── Callback wiring ─────────────────────────────────────────────────

    def _build_callbacks(self, quit_event: asyncio.Event) -> AgentCallbacks:
        """Assemble all agent→UI callbacks."""
        renderer = self._renderer

        async def _approval_cb(
            tool_call: ToolCall,
        ) -> Literal["approve", "approve_always", "deny"]:
            from vico.cli import request_approval

            return await request_approval(
                tool_call, renderer, self._session, quit_event, self._agent.cancel_event
            )

        def _on_skill_activated(meta: SkillMeta) -> None:
            console.print(
                f"  [cyan]\u25b6[/cyan]  Skill [bold cyan]{meta.name}[/bold cyan] activated — "
                f"[dim]{meta.description.splitlines()[0]}[/dim]"
            )

        return AgentCallbacks(
            on_thinking=renderer.on_thinking,
            on_text=renderer.on_text,
            on_tool_call=renderer.on_tool_call,
            on_tool_result=renderer.on_tool_result,
            on_error=renderer.on_error,
            on_done=lambda pt, ct: renderer.on_done_with_usage(pt, ct),
            on_loop=renderer.on_loop,
            on_skill_activated=_on_skill_activated,
            request_approval=_approval_cb,
        )

    # ─── Public API ──────────────────────────────────────────────────────

    async def run(self) -> None:
        """Print welcome banner and start the interactive REPL."""
        loop = asyncio.get_running_loop()
        self._renderer.print_welcome()

        try:
            from vico.cli import repl

            await repl(
                self._agent,
                self._renderer,
                self._session,
                loop,
                self._quit_event,
                self._config,
                self._permissions,
                skill_loader=self._skill_loader,
            )
        finally:
            await self._agent.aclose()

    # ─── /model command helper (exposed for CLI command use) ────────────

    @property
    def agent(self) -> AgentLoop:
        return self._agent

    @property
    def renderer(self) -> TerminalRenderer:
        return self._renderer

    @property
    def config(self) -> AgentConfig:
        return self._config

    @property
    def permissions(self) -> PermissionController:
        return self._permissions
