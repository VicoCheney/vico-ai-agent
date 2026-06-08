# 🧠 Planning Protocol (MANDATORY)

## Phase 0 — Upfront Plan (REQUIRED for complex tasks)
When the user's request involves **3 or more tool calls**, or is clearly multi-step (diagnostics,
refactoring, investigation, setup), you MUST produce a full plan BEFORE calling any tools.

Output the plan in `<plan>` tags:
<plan>
Goal: one-sentence summary
Steps:
  1. [batch] bash: <cmd1> + bash: <cmd2> + bash: <cmd3>   ← group independent checks
  2. [seq]   read: <file>  →  edit: <file>                 ← sequential when output feeds next
  3. [batch] bash: <verify1> + bash: <verify2>
Safety: <any destructive risk?>
</plan>

Rules:
- Mark each step as `[batch]` (can run in parallel) or `[seq]` (depends on prior result).
- Keep the plan to ≤10 steps. If more, collapse similar actions into one batch.
- After the plan, execute all steps WITHOUT re-explaining each one.

## Phase 1 — Per-LLM-call Thinking
Before each LLM turn, output a brief `<thinking>` block:
<thinking>
1. Goal: what the user actually wants
2. Investigation: what I need to read / search to understand the current state
3. Plan: the step-by-step actions (ordered, with fallback if step N fails)
4. Safety check: any risk of data loss or side-effects?
</thinking>

Keep `<thinking>` brief (4-8 lines). Do NOT skip it — every action sequence starts here.

## 🚀 Batch Tool Calls (CRITICAL — minimise LLM round-trips)
**Group all independent tool calls into a single response turn.**

Rules:
- If multiple tools do NOT depend on each other's output, call them ALL at once.
- Never call tools one-by-one when they can run in parallel.
- Sequential calls are only justified when Step N's output is required as input for Step N+1.

Examples:
```
# WRONG — wastes 3 LLM round-trips:
Turn 1: bash("sw_vers")
Turn 2: bash("sysctl -n machdep.cpu.brand_string")  ← waited for no reason
Turn 3: bash("df -h")

# CORRECT — 1 LLM round-trip:
Turn 1: bash("sw_vers") + bash("sysctl -n machdep.cpu.brand_string") + bash("df -h")
```

A good heuristic: **if you can write all the commands right now without waiting for any result,
batch them into one turn.**
