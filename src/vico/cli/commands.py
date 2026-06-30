"""
CLI REPL command handlers.

Extracted from ``cli/__init__.py`` so the entry-point module stays thin
and each command is independently importable / testable.
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

from rich.console import Console

from vico.config import lookup_provider
from vico.config.types.config import AgentConfig, LLMConfig
from vico.core.agent_loop import AgentLoop
from vico.core.permission_controller import PermissionController
from vico.exceptions import VicoError
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
        provider_config = lookup_provider(provider)
    except VicoError as e:
        console.print(f"  [red]✗[/red]  {e}")
        return

    if not provider_config["api_key"]:
        console.print(
            f"  [red]✗[/red]  No API key for '{provider}'.  Set [dim]{provider_config['api_key_env']}[/dim] in .env"
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
    except VicoError as e:
        console.print(f"  [red]✗[/red]  {e}")
        return

    agent.switch_model(new_llm)
    config.llm.provider = provider_config["provider"]
    config.llm.model = model
    config.llm.base_url = provider_config["base_url"]
    renderer.set_model_label(provider_config["provider"], model)
    permissions.clear_session_approvals()
    console.print(f"  [green]✓[/green]  Switched to [cyan]{provider}/{model}[/cyan]")
    console.print("  [dim]New model takes effect from the next message.[/dim]")
    console.print("  [dim]Session tool approvals have been reset.[/dim]")
