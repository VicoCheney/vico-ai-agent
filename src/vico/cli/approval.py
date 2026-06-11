"""
CLI approval dialog — permission-request UI components.

Extracted from ``cli/__init__.py`` to break the circular import between
``cli/__init__`` (imports VicoSession) and ``cli/session.py`` (needs request_approval).
"""

from __future__ import annotations

import asyncio
from typing import Literal

from prompt_toolkit import PromptSession
from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl

from vico.cli.renderer import TerminalRenderer
from vico.tools.types.call import ToolCall

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
    done_future: asyncio.Future[int] = asyncio.get_running_loop().create_future()

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
        erase_when_done=True,
    )

    app_task = asyncio.create_task(app.run_async())
    cancel_waiter = asyncio.create_task(cancel_event.wait())
    quit_waiter = asyncio.create_task(quit_event.wait())
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
