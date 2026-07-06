"""
CLI REPL command handlers.

Extracted from ``cli/__init__.py`` so the entry-point module stays thin
and each command is independently importable / testable.
"""

from __future__ import annotations

import asyncio
import shutil
from typing import TYPE_CHECKING, cast

from rich.console import Console

from vico.config import load_llm_config
from vico.config.types.config import AgentConfig, ContextStats
from vico.core.agent_loop import AgentLoop
from vico.core.permission_controller import PermissionController
from vico.exceptions import VicoError
from vico.llm.base import LLM
from vico.llm.llm_factory import create_llm_from_config
from vico.skills.loader import SkillLoader

if TYPE_CHECKING:
    from vico.cli.renderer import TerminalRenderer

console = Console()


# ─── /help ────────────────────────────────────────────────────────────────────


def print_help() -> None:
    w = shutil.get_terminal_size(fallback=(80, 24)).columns
    div = f"[dim]{'─' * w}[/dim]"
    console.print()
    console.print(div)
    console.print("[bold]  Commands[/bold]")
    console.print(div)
    cmds = [
        ("/clear", "Clear conversation history"),
        ("/model", "Show current provider & model"),
        ("/model <p/m>", "Switch model  e.g. deepseek/deepseek-v4-pro"),
        ("/skills", "List all available skills"),
        ("/skill <id> [args]", "Manually activate a skill with optional arguments"),
        ("/yolo", "Auto-approve all tools for this session"),
        ("/debug <topic>", "Show runtime diagnostics: context/tools/approvals/skills"),
        ("/help", "Show this message"),
        ("/exit", "Exit Vico"),
    ]
    for cmd, desc in cmds:
        console.print(f"  [cyan]{cmd:<26}[/cyan][dim]{desc}[/dim]")
    console.print()
    console.print("[bold]  Tips[/bold]")
    tips = [
        "Vico can read files, search code, and run shell commands",
        "High-risk commands require your approval before running",
        "Place SKILL.md files in .vico/skills/<name>/ to add custom skills",
        "Enter to send · Alt+Enter or Ctrl+J to insert a newline",
        "Ctrl+C during response to stop  ·  Ctrl+C when idle to exit",
    ]
    for tip in tips:
        console.print(f"  [dim]•  {tip}[/dim]")
    console.print(div)
    console.print()


# ─── /skills ──────────────────────────────────────────────────────────────────


def handle_skills_command(skill_loader: SkillLoader) -> None:
    w = shutil.get_terminal_size(fallback=(80, 24)).columns
    div = f"[dim]{'─' * w}[/dim]"

    metas = [m for m in skill_loader.get_all_metas() if m.user_invocable]
    if not metas:
        console.print()
        console.print("  [dim]No skills found.[/dim]")
        console.print("  [dim]Place SKILL.md files in [cyan].vico/skills/<name>/[/cyan] to add skills.[/dim]")
        console.print()
        return

    console.print()
    console.print(div)
    console.print("[bold]  Available Skills[/bold]")
    console.print(div)
    for meta in metas:
        hint = f" [dim]{meta.argument_hint}[/dim]" if meta.argument_hint else ""
        lock = " [yellow](manual only)[/yellow]" if meta.disable_model_invocation else ""
        tools = f" [dim]tools:{','.join(meta.allowed_tools)}[/dim]" if meta.allowed_tools else ""
        risk = f" [dim]risk:{meta.risk_level}[/dim]"
        source = f" [dim]source:{meta.source}[/dim]"
        cmd = f"/skill {meta.skill_id}{hint}"
        desc_line = meta.description.splitlines()[0] if meta.description else ""
        console.print(f"  [cyan]{cmd:<28}[/cyan]{lock}{source}{risk}{tools}  [dim]{desc_line}[/dim]")
    console.print(div)
    console.print()


def handle_skill_command(user_input: str, agent: AgentLoop, skill_loader: SkillLoader) -> None:
    parts = user_input.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        console.print("  [dim]Usage: /skill <skill-id> [arguments][/dim]")
        handle_skills_command(skill_loader)
        return

    skill_parts = parts[1].strip().split(maxsplit=1)
    skill_id = skill_parts[0]
    arguments = skill_parts[1] if len(skill_parts) > 1 else ""
    ok = agent.inject_skill_by_id(skill_id, arguments=arguments)
    if ok:
        console.print(f"  [green]✓[/green]  Skill [cyan]{skill_id}[/cyan] loaded into context.")
        console.print("  [dim]The skill instructions are now available for the next message.[/dim]")
    else:
        console.print(f"  [red]✗[/red]  Skill not found: [cyan]{skill_id}[/cyan]")
        handle_skills_command(skill_loader)


# ─── /yolo ───────────────────────────────────────────────────────────────────


def handle_yolo_command(permissions: PermissionController) -> None:
    permissions.enable_yolo_mode()
    console.print("  [green]✓[/green]  YOLO mode enabled for this session.")
    console.print("  [yellow]All subsequent tool calls will run without approval prompts.[/yellow]")


# ─── /debug ──────────────────────────────────────────────────────────────────


def handle_debug_command(
    user_input: str,
    agent: AgentLoop,
    permissions: PermissionController,
    skill_loader: SkillLoader | None = None,
) -> None:
    parts = user_input.split(maxsplit=1)
    topic = parts[1].strip() if len(parts) > 1 else "help"
    snapshot = agent.debug_snapshot()

    if topic == "context":
        stats = cast(ContextStats, snapshot["context"])
        messages = cast(list[dict[str, str | int]], snapshot["messages"])
        console.print()
        console.print("[bold]  Debug: Context[/bold]")
        console.print(
            f"  [dim]state[/dim]     [cyan]{snapshot['state']}[/cyan]\n"
            f"  [dim]messages[/dim]  [cyan]{stats.message_count}[/cyan]\n"
            f"  [dim]tokens[/dim]    [cyan]{stats.estimated_tokens}/{stats.max_tokens}[/cyan]\n"
            f"  [dim]usage[/dim]     [cyan]{stats.usage_percent:.1f}%[/cyan]"
        )
        console.print("  [dim]recent messages[/dim]")
        for msg in messages:
            console.print(
                f"    [cyan]{str(msg['role']):<9}[/cyan] "
                f"[dim]{msg['id']}[/dim]  {msg['preview']}"
            )
        console.print()
        return

    if topic == "tools":
        tools = cast(list[dict[str, str]], snapshot["tools"])
        console.print()
        console.print("[bold]  Debug: Tools[/bold]")
        for tool in tools:
            console.print(
                f"  [cyan]{tool['name']:<16}[/cyan]"
                f"[dim]risk:{tool['risk']:<6}[/dim]  [dim]{tool['description']}[/dim]"
            )
        console.print()
        return

    if topic == "approvals":
        approvals = permissions.describe_session_approvals()
        console.print()
        console.print("[bold]  Debug: Session Approvals[/bold]")
        console.print(
            f"  [dim]yolo_mode[/dim]  "
            f"[cyan]{permissions.yolo_mode_enabled()}[/cyan]"
        )
        if not approvals:
            console.print("  [dim]No session approvals.[/dim]")
        for item in approvals:
            console.print(f"  [cyan]{item['tool']:<16}[/cyan][dim]{item['input_fingerprint']}[/dim]")
        console.print()
        return

    if topic == "skills" and skill_loader:
        console.print()
        console.print("[bold]  Debug: Skills[/bold]")
        for meta in skill_loader.get_all_metas():
            manual = " manual-only" if meta.disable_model_invocation else ""
            console.print(
                f"  [cyan]{meta.skill_id:<24}[/cyan]"
                f"[dim]source:{meta.source} risk:{meta.risk_level}{manual} path:{meta.skill_dir}[/dim]"
            )
        console.print()
        return

    console.print()
    console.print("[bold]  Debug Topics[/bold]")
    for topic_name in ("context", "tools", "approvals", "skills"):
        console.print(f"  [cyan]/debug {topic_name}[/cyan]")
    console.print()


# ─── /model ───────────────────────────────────────────────────────────────────


def handle_model_command(
    user_input: str,
    agent: AgentLoop,
    renderer: TerminalRenderer,
    config: AgentConfig,
    permissions: PermissionController,
) -> None:
    from vico.cli.renderer import TerminalRenderer as _TerminalRenderer  # noqa: F401 local guard

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
        model = model.strip()
    else:
        provider = config.llm.provider
        model = arg

    try:
        new_llm_config = load_llm_config(provider, model, cwd=config.cwd)
        new_llm = create_llm_from_config(new_llm_config)
    except VicoError as e:
        console.print(f"  [red]✗[/red]  {e}")
        return

    old_llm = agent.llm
    agent.switch_model(new_llm)
    config.llm = new_llm_config
    renderer.set_model_label(new_llm_config.provider, new_llm_config.model)
    permissions.clear_session_approvals()
    _close_llm_background(old_llm)
    console.print(f"  [green]✓[/green]  Switched to [cyan]{new_llm_config.provider}/{new_llm_config.model}[/cyan]")
    console.print("  [dim]New model takes effect from the next message.[/dim]")
    console.print("  [dim]Session tool approvals have been reset.[/dim]")


def _close_llm_background(llm: LLM) -> None:
    async def _close() -> None:
        try:
            await llm.aclose()
        except Exception:
            pass

    try:
        asyncio.create_task(_close())
    except RuntimeError:
        pass
