"""
Interactive REPL loop for the Vico CLI.

Extracted from ``cli/__init__.py`` to break the circular import between
``cli/__init__`` (imports VicoSession) and ``cli/session.py`` (calls repl).
"""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Callable

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console

from vico.cli import theme
from vico.cli.commands import (
    handle_model_command,
    handle_skill_command,
    handle_skills_command,
    print_help,
)
from vico.cli.renderer import TerminalRenderer
from vico.config.types.config import AgentConfig
from vico.core.agent_loop import AgentLoop
from vico.core.permission_controller import PermissionController
from vico.skills.loader import SkillLoader

console = Console()

_PROMPT_STR = ANSI(f"\n{theme.DIM}👤 You: {theme.RESET}")
_PROMPT_CONT = ANSI(f"{theme.DIM}         {theme.RESET}")


def _make_prompt_key_bindings(
    quit_event: asyncio.Event,
    cancel_event: asyncio.Event | None = None,
) -> KeyBindings:
    """Build key bindings: Enter = submit, Alt+Enter/Ctrl+J = newline, Ctrl+C = cancel."""
    kb = KeyBindings()

    @kb.add("c-c")
    def _ctrl_c(event):  # type: ignore[no-untyped-def]
        quit_event.set()
        if cancel_event is not None:
            cancel_event.set()
        event.app.exit(result="")

    @kb.add("enter")
    def _submit(event):  # type: ignore[no-untyped-def]
        event.app.current_buffer.validate_and_handle()

    @kb.add("escape", "enter", eager=True)
    @kb.add("c-j")
    def _newline(event):  # type: ignore[no-untyped-def]
        event.app.current_buffer.insert_text("\n")

    return kb


def _set_sigint(loop: asyncio.AbstractEventLoop, handler: Callable[[], None]) -> None:
    """Register SIGINT handler on the event loop (no-op where unsupported)."""
    try:
        loop.add_signal_handler(signal.SIGINT, handler)
    except (NotImplementedError, AttributeError):
        pass


async def repl(
    agent: AgentLoop,
    renderer: TerminalRenderer,
    session: PromptSession,  # type: ignore[type-arg]
    loop: asyncio.AbstractEventLoop,
    quit_event: asyncio.Event,
    config: AgentConfig,
    permissions: PermissionController,
    skill_loader: SkillLoader | None = None,
) -> None:
    """Interactive REPL loop."""
    prompt_kb = _make_prompt_key_bindings(quit_event)

    def _idle_sigint() -> None:
        console.print()
        renderer.print_goodbye()
        quit_event.set()

    _set_sigint(loop, _idle_sigint)

    while not quit_event.is_set():
        renderer.print_divider()

        try:
            with patch_stdout(raw=True):
                user_input = await session.prompt_async(
                    _PROMPT_STR,
                    prompt_continuation=_PROMPT_CONT,
                    multiline=True,
                    key_bindings=prompt_kb,
                )
        except (EOFError, KeyboardInterrupt):
            renderer.print_goodbye()
            quit_event.set()
            break

        if quit_event.is_set():
            renderer.print_goodbye()
            break

        user_input = user_input.strip()

        if not user_input:
            continue

        if user_input in ("/exit", "/quit"):
            renderer.print_goodbye()
            quit_event.set()
            return

        if user_input == "/clear":
            agent.reset()
            console.clear()
            renderer.print_welcome()
            _set_sigint(loop, _idle_sigint)
            continue

        if user_input == "/help":
            print_help()
            continue

        if user_input.startswith("/model"):
            handle_model_command(user_input, agent, renderer, config, permissions)
            continue

        if user_input == "/skills" and skill_loader:
            handle_skills_command(skill_loader)
            continue

        if user_input.startswith("/skill ") and skill_loader:
            handle_skill_command(user_input, agent, skill_loader)
            continue

        # Run the agent
        renderer.reset_output_state()
        renderer.start_waiting()
        run_task: asyncio.Task[None] = asyncio.create_task(agent.run(user_input, max_iterations=30))
        aborted = False

        def _run_sigint() -> None:
            nonlocal aborted
            if run_task.done():
                _idle_sigint()
                return
            aborted = True
            agent.cancel()
            run_task.cancel()
            quit_event.set()

        _set_sigint(loop, _run_sigint)

        try:
            await run_task
        except asyncio.CancelledError:
            pass
        finally:
            await renderer.flush_async()
            if aborted:
                renderer.print_aborted()
            _set_sigint(loop, _idle_sigint)

        if quit_event.is_set():
            break

        stats = agent.get_context_stats()
        renderer.print_context_stats(stats)
