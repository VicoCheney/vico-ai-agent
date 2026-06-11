"""
CLI Entry Point — thin orchestration shell.

Heavy lifting is delegated to:
  cli/approval.py   — request_approval dialog
  cli/commands.py   — /help, /model, /skill[s] command handlers
  cli/repl.py       — interactive REPL loop
  cli/session.py    — VicoSession (object graph assembly + REPL runner)
"""

from __future__ import annotations

import asyncio
import os

from rich.console import Console

from vico.cli.approval import request_approval
from vico.cli.repl import repl
from vico.cli.session import VicoSession
from vico.config import load_config
from vico.exceptions import VicoError

console = Console()

__all__ = [
    "async_main",
    "main",
    "request_approval",
    "repl",
    "VicoSession",
]


async def async_main() -> None:
    try:
        config = load_config(cwd=os.getcwd())
    except (ValueError, VicoError) as exc:
        console.print(f"\n[bold red]Configuration Error:[/bold red] {exc}\n")
        raise SystemExit(1) from exc

    session = VicoSession(config)
    await session.run()


def main() -> None:
    try:
        asyncio.run(async_main())
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
