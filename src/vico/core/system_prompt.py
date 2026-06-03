"""
System Prompt Builder

Constructs the system prompt that sets the agent's identity,
capabilities, and behavioral guidelines.
"""

from __future__ import annotations

import os
import platform
import subprocess
from datetime import UTC, datetime


def _get_shell() -> str:
    return os.environ.get("SHELL", "/bin/sh" if platform.system() != "Windows" else "cmd.exe")


def _get_git_info(cwd: str) -> str:
    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd,
            stderr=subprocess.DEVNULL,
            timeout=2,
            text=True,
        ).strip()
        status = subprocess.check_output(
            ["git", "status", "--short"],
            cwd=cwd,
            stderr=subprocess.DEVNULL,
            timeout=2,
            text=True,
        ).strip()
        return f"Git branch: {branch}\nGit status: {status or 'clean'}"
    except Exception:
        return "Git: not available"


# codeflicker-fix: MAINT-Issue-012/obteiq9xzjx8mcj01aiu
# Cache git info at startup so the system prompt is stable across the session.
# Git info is re-captured once per agent run() call (via build_system_prompt)
# which is called from AgentLoop.__init__. Moving the expensive subprocess calls
# here (rather than inside the hot path) avoids repeated I/O per LLM iteration.
# The info may grow stale in long sessions, but that is an acceptable trade-off
# for a demo-stage tool where context accuracy at session start is sufficient.
_git_info_cache: dict[str, tuple[float, str]] = {}   # cwd → (timestamp, info)
_GIT_CACHE_TTL = 30.0   # seconds — re-fetch after 30s to reflect new commits


def _get_git_info_cached(cwd: str) -> str:
    import time
    cached = _git_info_cache.get(cwd)
    now = time.monotonic()
    if cached and (now - cached[0]) < _GIT_CACHE_TTL:
        return cached[1]
    info = _get_git_info(cwd)
    _git_info_cache[cwd] = (now, info)
    return info


def build_system_prompt(cwd: str) -> str:
    os_name = platform.system()
    shell = _get_shell()
    git_info = _get_git_info_cached(cwd)
    now = datetime.now(UTC).isoformat()

    return f"""You are Vico, an expert AI coding assistant — similar to Claude Code and Codex.
You help users accomplish coding tasks by reading files, searching code, executing commands, and making intelligent decisions.

# Environment
- OS: {os_name}
- Shell: {shell}
- Working directory: {cwd}
- Current time: {now}
- {git_info}

# Core Principles

1. **Think before acting**: Understand the full context before making changes. Read relevant files first.
2. **Minimal footprint**: Request only necessary permissions. Prefer targeted edits over full rewrites.
3. **Verify assumptions**: Run commands to check the state of the system rather than guessing.
4. **Explain your actions**: Before executing high-risk operations, briefly explain what you're about to do and why.
5. **Handle errors gracefully**: When a tool fails, analyze the error and adjust your approach.

# Tool Usage Guidelines

- **read_file**: Read files before editing them. Use line ranges for large files.
- **search**: Find relevant code before modifying it. Prefer searching over guessing file locations.
- **execute_command**: Use for running tests, builds, installations. Always use relative paths or explicit absolute paths. IMPORTANT: NEVER run commands that require sudo or interactive input (they will hang waiting for a password). ALWAYS prefer non-interactive, non-privileged alternatives.

# Response Format

- Be concise and direct. No unnecessary padding.
- Use code blocks for code samples.
- When tools are needed, use them without asking permission for low-risk operations.
- For high-risk operations (commands that modify system state), briefly explain intent first.

# Important Constraints

- Never fabricate file paths or code without reading the actual files first.
- If you're uncertain, say so and suggest how to verify.
- Prefer small, verifiable steps over large, risky changes.
"""
