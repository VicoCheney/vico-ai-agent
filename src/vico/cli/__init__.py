"""
CLI Entry Point

Input: Uses prompt-toolkit for readline input with full CJK/unicode support.
Ctrl+C is handled via key bindings during prompt and via SIGINT during agent execution.

Conversation layout per turn:
  ─────────────────────────────────────────────────────────
   │  You  message text

   │  Vico
  │ 💭 thinking...
  tool lines...
  response text...
  ── context N% ────────────────────────────────────────────
  ─────────────────────────────────────────────────────────
"""

from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import Callable
from typing import Literal

from prompt_toolkit import PromptSession
from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console

from vico.cli.renderer import TerminalRenderer
from vico.config import load_config, lookup_provider
from vico.core.agent_loop import AgentCallbacks, AgentLoop
from vico.core.context_manager import ContextManager
from vico.core.permission_controller import PermissionController
from vico.core.types import AgentConfig, LLMConfig, ToolCall
from vico.llm.llm_factory import create_llm_from_config
from vico.tools import BUILTIN_TOOLS
from vico.tools.registry import ToolRegistry

console = Console()

_RESET      = "\033[0m"
_DIM        = "\033[2m"
_BOLD       = "\033[1m"
_BRIGHT_BLK = "\033[90m"
_WHITE_BOLD = "\033[1;37m"

_PROMPT_STR = ANSI(
    f"\n{_BRIGHT_BLK}👤 You: {_RESET}"
)


# ─── Ctrl+C key binding for prompt-toolkit ───────────────────────────────────


def _make_prompt_key_bindings(
    quit_event: asyncio.Event,
    cancel_event: asyncio.Event | None = None,
) -> KeyBindings:
    kb = KeyBindings()

    @kb.add("c-c")
    def _ctrl_c(event):  # type: ignore[no-untyped-def]
        quit_event.set()
        if cancel_event is not None:
            cancel_event.set()
        event.app.exit(result="")

    return kb


# ─── Signal helpers ───────────────────────────────────────────────────────────


def _set_sigint(loop: asyncio.AbstractEventLoop, handler: Callable[[], None]) -> None:
    try:
        loop.add_signal_handler(signal.SIGINT, handler)
    except (NotImplementedError, AttributeError):
        pass


# ─── Permission approval ──────────────────────────────────────────────────────

_APPROVAL_OPTIONS: list[tuple[str, Literal["approve", "approve_always", "deny"]]] = [
    ("  Once  ", "approve"),
    (" Always ", "approve_always"),
    ("  Deny  ", "deny"),
]


async def _run_selector(
    quit_event: asyncio.Event,
    cancel_event: asyncio.Event,
) -> Literal["approve", "approve_always", "deny"]:
    """Display a left/right arrow-key selector and return the user's decision."""
    selected: list[int] = [0]
    done_future: asyncio.Future[int] = asyncio.get_event_loop().create_future()

    option_labels = [label for label, _ in _APPROVAL_OPTIONS]
    n = len(option_labels)

    def _render_selector() -> list[tuple[str, str]]:
        parts: list[tuple[str, str]] = [
            ("fg:yellow bold", "  > "),
            ("fg:ansibrightyellow", "Allow?  "),
        ]
        for i, label in enumerate(option_labels):
            if i > 0:
                parts.append(("fg:gray", "  |  "))
            if i == selected[0]:
                parts.append(("fg:green bold reverse", label))
            else:
                parts.append(("fg:gray", label))
        parts.append(("", "\n"))
        return parts

    kb = KeyBindings()

    @kb.add("right")
    @kb.add("tab")
    def _next(event):  # type: ignore[no-untyped-def]
        selected[0] = (selected[0] + 1) % n
        event.app.invalidate()

    @kb.add("left")
    @kb.add("s-tab")
    def _prev(event):  # type: ignore[no-untyped-def]
        selected[0] = (selected[0] - 1) % n
        event.app.invalidate()

    @kb.add("enter")
    @kb.add("c-j")
    def _confirm(event):  # type: ignore[no-untyped-def]
        if not done_future.done():
            done_future.set_result(selected[0])
        event.app.exit()

    @kb.add("c-c")
    @kb.add("c-d")
    def _cancel(event):  # type: ignore[no-untyped-def]
        quit_event.set()
        cancel_event.set()
        if not done_future.done():
            done_future.set_result(2)  # index 2 = Deny
        event.app.exit()

    app: Application = Application(  # type: ignore[type-arg]
        layout=Layout(Window(content=FormattedTextControl(_render_selector))),
        key_bindings=kb,
        full_screen=False,
        refresh_interval=None,
        # Erase the selector widget when the app exits so the summary line
        # written by collapse_permission_request() lands in the correct position.
        erase_when_done=True,
    )

    app_task = asyncio.ensure_future(app.run_async())
    cancel_waiter = asyncio.ensure_future(cancel_event.wait())
    quit_waiter   = asyncio.ensure_future(quit_event.wait())
    done, pending = await asyncio.wait(
        {app_task, cancel_waiter, quit_waiter},
        return_when=asyncio.FIRST_COMPLETED,
    )
    for t in pending:
        t.cancel()
    if not app_task.done():
        app.exit()

    if done_future.done():
        idx = done_future.result()
    else:
        idx = 2  # Deny on external cancel

    return _APPROVAL_OPTIONS[idx][1]


async def request_approval(
    tool_call: ToolCall,
    renderer: TerminalRenderer,
    session: PromptSession,  # type: ignore[type-arg]
    quit_event: asyncio.Event,
    cancel_event: asyncio.Event,
) -> Literal["approve", "approve_always", "deny"]:
    renderer.print_permission_request(tool_call)

    if cancel_event.is_set() or quit_event.is_set():
        renderer.collapse_permission_request("deny")
        return "deny"

    decision = await _run_selector(quit_event, cancel_event)
    renderer.collapse_permission_request(decision)
    return decision


# ─── Help ─────────────────────────────────────────────────────────────────────


def print_help() -> None:
    console.print()
    console.print("[dim]──────────────────────────────────────[/dim]")
    console.print("[bold]  Commands[/bold]")
    console.print("[dim]──────────────────────────────────────[/dim]")
    cmds = [
        ("/clear",              "Clear conversation history"),
        ("/model",              "Show current provider & model"),
        ("/model <p/m>",        "Switch model  e.g. deepseek/deepseek-v4-pro"),
        ("/help",               "Show this message"),
        ("/exit",               "Exit Vico"),
    ]
    for cmd, desc in cmds:
        console.print(f"  [cyan]{cmd:<26}[/cyan][dim]{desc}[/dim]")
    console.print()
    console.print("[bold]  Tips[/bold]")
    tips = [
        "Vico can read files, search code, and run shell commands",
        "High-risk commands require your approval before running",
        "Ctrl+C during response to stop  ·  Ctrl+C when idle to exit",
    ]
    for tip in tips:
        console.print(f"  [dim]•  {tip}[/dim]")
    console.print("[dim]──────────────────────────────────────[/dim]")
    console.print()


# ─── /model command ───────────────────────────────────────────────────────────


def _handle_model_command(
    user_input: str,
    agent: AgentLoop,
    renderer: TerminalRenderer,
    config: AgentConfig,
    permissions: PermissionController,
) -> None:
    parts = user_input.split(maxsplit=1)

    if len(parts) == 1:
        console.print(
            f"  [dim]provider[/dim]  [cyan]{config.llm.provider}[/cyan]\n"
            f"  [dim]model   [/dim]  [cyan]{config.llm.model}[/cyan]\n"
            f"  [dim]usage   [/dim]  /model <provider/model>"
        )
        return

    arg = parts[1].strip()
    if "/" in arg:
        provider, model = arg.split("/", 1)
        provider = provider.strip()
        model    = model.strip()
    else:
        provider = config.llm.provider
        model    = arg

    try:
        provider_config = lookup_provider(provider)
    except ValueError as e:
        console.print(f"  [red]✗[/red]  {e}")
        return

    if not provider_config["api_key"]:
        console.print(
            f"  [red]✗[/red]  No API key for '{provider}'.  "
            f"Set [dim]{provider_config['api_key_env']}[/dim] in .env"
        )
        return

    try:
        new_llm = create_llm_from_config(
            LLMConfig(
                provider=provider_config["provider"],
                api_key=provider_config["api_key"],
                base_url=provider_config["base_url"],
                model=model,
                max_tokens=config.llm.max_tokens,
                temperature=config.llm.temperature,
            )
        )
    except ValueError as e:
        console.print(f"  [red]✗[/red]  {e}")
        return

    agent.switch_model(new_llm)
    config.llm.provider = provider_config["provider"]
    config.llm.model    = model
    config.llm.base_url = provider_config["base_url"]
    renderer.set_model_label(provider_config["provider"], model)
    permissions.clear_session_approvals()
    console.print(f"  [green]✓[/green]  Switched to [cyan]{provider}/{model}[/cyan]")
    console.print("  [dim]New model takes effect from the next message.[/dim]")
    console.print("  [dim]Session tool approvals have been reset.[/dim]")


# ─── REPL ──────────────────────────────────────────────────────────────────────


async def repl(
    agent: AgentLoop,
    renderer: TerminalRenderer,
    session: PromptSession,  # type: ignore[type-arg]
    loop: asyncio.AbstractEventLoop,
    quit_event: asyncio.Event,
    config: AgentConfig,
    permissions: PermissionController,
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
            _handle_model_command(user_input, agent, renderer, config, permissions)
            continue

        # ── Run the agent ─────────────────────────────────────────────────────
        renderer.reset_output_state()
        run_task: asyncio.Task[None] = asyncio.create_task(
            agent.run(user_input, max_iterations=30)
        )
        aborted = False

        def _run_sigint() -> None:
            nonlocal aborted
            if run_task.done():
                _idle_sigint()
                return
            aborted = True
            agent.cancel()
            if agent.state == "waiting_approval":
                run_task.cancel()
            quit_event.set()

        _set_sigint(loop, _run_sigint)

        try:
            await run_task
        except asyncio.CancelledError:
            pass
        finally:
            renderer.flush()
            if aborted:
                renderer.print_aborted()
            _set_sigint(loop, _idle_sigint)

        if quit_event.is_set():
            break

        stats = agent._context.get_stats("")
        renderer.print_context_stats(stats)


# ─── Main ─────────────────────────────────────────────────────────────────────


async def async_main() -> None:
    loop = asyncio.get_event_loop()

    try:
        config = load_config(cwd=os.getcwd())
    except ValueError as exc:
        console.print(f"\n[bold red]Configuration Error:[/bold red] {exc}\n")
        raise SystemExit(1) from exc

    renderer = TerminalRenderer()
    renderer.set_model_label(config.llm.provider, config.llm.model)
    renderer.set_cwd(config.cwd)

    quit_event = asyncio.Event()
    session: PromptSession = PromptSession()

    tool_registry = ToolRegistry()
    tool_registry.register_all(BUILTIN_TOOLS)

    llm = create_llm_from_config(config)

    context = ContextManager(
        max_tokens=config.context.max_tokens,
        reserve_tokens=config.context.reserve_tokens,
        compression_threshold=config.context.compression_threshold,
    )
    permissions = PermissionController(auto_approve_risks=config.tools.auto_approve)

    async def _approval_cb(
        tool_call: ToolCall,
    ) -> Literal["approve", "approve_always", "deny"]:
        return await request_approval(
            tool_call, renderer, session, quit_event, agent._cancel_event
        )

    callbacks = AgentCallbacks(
        on_thinking=renderer.on_thinking,
        on_text=renderer.on_text,
        on_tool_call=renderer.on_tool_call,
        on_tool_result=renderer.on_tool_result,
        on_error=renderer.on_error,
        on_done=lambda pt, ct: renderer.on_done_with_usage(pt, ct),
        on_loop=renderer.on_loop,
        request_approval=_approval_cb,
    )

    agent = AgentLoop(
        llm=llm,
        context=context,
        tool_registry=tool_registry,
        permissions=permissions,
        config=config,
        callbacks=callbacks,
    )

    renderer.print_welcome()

    await repl(agent, renderer, session, loop, quit_event, config, permissions)


def main() -> None:
    try:
        asyncio.run(async_main())
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
