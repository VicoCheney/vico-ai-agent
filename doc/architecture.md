# Vico AI Agent — 当前核心架构

> 本文档以当前代码实现为准，描述 Vico Agent 的运行链路、工具调度、Skill 加载、配置与关键设计取舍。

---

## 目录

1. [设计哲学](#设计哲学)
2. [总体架构](#总体架构)
3. [Agent Loop](#agent-loop)
4. [批量工具调用机制](#批量工具调用机制)
5. [权限与工具执行](#权限与工具执行)
6. [Skill 懒加载机制](#skill-懒加载机制)
7. [上下文管理](#上下文管理)
8. [配置参考](#配置参考)
9. [代码索引](#代码索引)
10. [设计决策记录](#设计决策记录adr)

---

## 设计哲学

Vico 的核心设计目标：

> **用尽可能少的 LLM round-trips，完成尽可能复杂的本地工程任务。**

当前实现不包含独立 Planner LLM，也没有复杂度检测分支。规划能力通过系统提示词要求模型在复杂任务前输出 `<plan>`，执行能力由同一个 Agent Loop 驱动。

| 维度 | 当前实现 |
|------|----------|
| 规划 | Prompt 内 Planning Protocol，引导模型先写 `<plan>` |
| 执行 | 同一个 LLM turn 可输出多个 structured tool calls |
| 并发 | 自动批准的工具批量 `asyncio.gather` |
| 安全 | 工具风险分级 + 审批回调 + 路径边界检查 |
| 扩展 | `.vico/skills` 与 `~/.vico/skills` 下的 SKILL.md 懒加载 |

---

## 总体架构

```
用户输入
   │
   ▼
CLI REPL
   │
   ▼
AgentLoop.run()
   │
   ├─ add_user_message()
   ├─ maybe_compress()
   │
   ▼
AgentLoop._loop()
   │
   ├─ LLM.stream(system_prompt + messages + tool_defs)
   │      ├─ TextChunk       → renderer.on_text()
   │      ├─ ReasoningChunk  → renderer.on_thinking()
   │      ├─ ToolCallChunk   → pending_tool_calls[]
   │      └─ DoneChunk       → usage stats
   │
   ├─ add_assistant_message(text, tool_calls)
   │
   ├─ 无工具调用?
   │      ├─ 若包含兼容标签 <use_skill>ID</use_skill> → 注入 Skill 正文，继续下一轮
   │      └─ 否则结束本次用户请求
   │
   └─ 有工具调用?
          ├─ 任一工具需要审批 → 串行执行
          └─ 全部自动批准     → asyncio.gather 并发执行
              │
              ▼
          add_tool_result() × N
          maybe_compress()
          回到下一轮 _loop()
```

---

## Agent Loop

**入口**：`src/vico/core/agent_loop.py` → `AgentLoop.run()`

当前主循环：

```python
async def run(self, user_input: str, max_iterations: int | None = None) -> None:
    self._context.add_user_message(user_input)
    self._context.maybe_compress(self._system_prompt)
    await self._loop(effective_max)
```

`_loop()` 每轮做五件事：

1. 调用 `_stream_llm()`，收集文本、推理片段和工具调用。
2. 将 assistant 文本和 tool calls 写入上下文。
3. 若没有工具调用，检查是否需要注入 Skill。
4. 若有工具调用，进入 `_dispatch_tool_calls()`。
5. 将工具结果写入上下文，并在需要时压缩上下文。

### Prompt 内规划

`src/vico/prompts/planning.md` 要求复杂任务先输出 `<plan>`：

```text
<plan>
Goal: ...
Steps:
  1. [batch] ...
  2. [seq] ...
Safety: ...
</plan>
```

这只是同一个 Executor LLM 的文本规划，不会触发单独的 Planner 请求，也不会写入专门的 `plan_summary` 消息。

---

## 批量工具调用机制

Vico 依赖模型一次输出多个 structured tool calls，并在本地批量调度。

```python
needs_approval = any(
    not self._permissions.is_auto_approved(tc, self._tool_registry)
    for tc in pending_tool_calls
)

if needs_approval:
    for tc in pending_tool_calls:
        result = await self._execute_one(tc)
else:
    raw_results = await asyncio.gather(
        *[self._execute_one(tc) for tc in pending_tool_calls],
        return_exceptions=True,
    )
```

### 并发 vs 串行

| 条件 | 执行方式 | 原因 |
|------|----------|------|
| 所有工具自动批准 | 并发执行 | 最大化吞吐，减少总耗时 |
| 任一工具需要审批 | 串行执行 | 避免多个确认框和终端 UI 互相干扰 |

### 工具结果

每个工具结果都会以 `role="tool"` 写回上下文：

```python
self._context.add_tool_result(
    tool_use_id=tool_call.id,
    tool_name=tool_call.name,
    content=result.output if result.success else (result.error or "Unknown error"),
    is_error=not result.success,
)
```

---

## 权限与工具执行

内置工具：

| 工具 | 风险级别 | 说明 |
|------|----------|------|
| `read` | low | 读取项目内文件，支持行范围 |
| `search` | low | 用 ripgrep/grep 搜索项目内文件 |
| `write` | medium | 创建或覆盖项目内文件 |
| `edit` | medium | 精确字符串替换项目内文件 |
| `bash` | high | 在项目目录内执行 Shell 命令 |
| `activate_skill` | low | 结构化加载 Skill 正文 |

审批由 `PermissionController` 控制：

- `auto_approve` 中的风险级别自动执行。
- 用户选择 `approve_always` 后，同名工具在本会话中自动批准。
- 未注册工具或未自动批准工具会走 `request_approval` 回调。

路径边界：

- `read` / `write` / `edit` / `search` 都拒绝访问项目根目录外的路径。
- `bash` 的 `cwd` override 也必须位于项目根目录内。

---

## Skill 懒加载机制

Skill 系统由 `src/vico/skills/loader.py` 与 `AgentLoop` 共同完成。

### 搜索路径

当前只支持 Vico 自有目录：

1. `<cwd>/.vico/skills/<skill-id>/SKILL.md`
2. `~/.vico/skills/<skill-id>/SKILL.md`

同名 Skill 由更高优先级路径覆盖。

### 加载阶段

| 阶段 | 行为 |
|------|------|
| 会话启动 | 扫描 `SKILL.md` frontmatter，只把 name/description 等元数据注入 system prompt |
| 模型触发 | 模型优先调用 `activate_skill` 工具；`<use_skill>SKILL_ID</use_skill>` 仅作兼容后备 |
| 用户触发 | 用户输入 `/skill <skill-id> [arguments]` |
| 激活后 | 将 Skill 正文包装为 `<skill_instructions>` 用户消息注入上下文，下一轮 LLM 可使用完整指令 |

### Frontmatter

当前支持字段：

```yaml
---
name: code-review
description: Review code changes and identify risks.
argument-hint: "[path]"
disable-model-invocation: false
user-invocable: true
allowed-tools: ["read", "search"]
risk-level: low
---
```

`disable-model-invocation: true` 表示模型不能自动激活，只能由用户显式 `/skill` 激活。

---

## 上下文管理

`ContextManager` 负责维护内存中的消息历史，并用估算 token 控制上下文大小。

当前压缩策略是保留最近消息，并插入一条 system note：

```text
[Context note: N earlier messages were summarized to save space. The conversation continues below.]
```

注意：当前实现是“裁剪 + 说明”，并没有调用 LLM 对旧消息做语义摘要。

---

## 配置参考

`.vicorc.json` 主要配置项：

```jsonc
{
  "providers": {
    "mimo": {
      "base_url": "https://token-plan-sgp.xiaomimimo.com/v1",
      "api_key_env": "MIMO_API_KEY",
      "default_model": "mimo-v2.5"
    },
    "deepseek": {
      "base_url": "https://api.deepseek.com",
      "api_key_env": "DEEPSEEK_API_KEY",
      "default_model": "deepseek-v4-flash"
    }
  },
  "llm": {
    "default": { "provider": "mimo", "model": "mimo-v2.5" }
  },
  "context": {
    "max_tokens": 1000000,
    "reserve_tokens": 131072,
    "compression_threshold": 0.85
  },
  "tools": {
    "auto_approve": ["low", "medium"],
    "timeout_ms": 30000,
    "env_whitelist": ["PATH", "HOME", "SHELL", "LANG", "TERM"]
  },
  "limits": {
    "max_iterations": 30
  }
}
```

---

## 代码索引

| 模块 | 文件 | 职责 |
|------|------|------|
| CLI 入口 | `src/vico/cli/__init__.py` | 加载配置并启动会话 |
| 会话组装 | `src/vico/cli/session.py` | 创建 renderer、LLM、tools、permissions、skills、AgentLoop |
| REPL | `src/vico/cli/repl.py` | 处理用户输入和 `/help`、`/model`、`/skills`、`/skill` 等命令 |
| 主循环 | `src/vico/core/agent_loop.py` | LLM streaming、工具批量调度、Skill 注入、上下文写回 |
| 上下文 | `src/vico/core/context_manager.py` | 消息历史、token 估算、上下文压缩 |
| 系统提示词 | `src/vico/core/system_prompt.py` | 渲染 Jinja2 Prompt，注入 runtime vars 和 Skill 摘要 |
| Prompt 模板 | `src/vico/prompts/*.md` | persona、环境、安全、规划、工具、响应格式 |
| 权限 | `src/vico/core/permission_controller.py` | 风险级别自动审批和会话审批 |
| LLM 工厂 | `src/vico/llm/llm_factory.py` | Provider 注册与创建 |
| Provider 共享层 | `src/vico/llm/providers/base.py` | OpenAI-compatible 消息、工具、流式 chunk 转换 |
| 工具注册 | `src/vico/tools/registry.py` | 注册并调度内置工具 |
| Skill 扫描 | `src/vico/skills/loader.py` | 扫描 `.vico/skills`，解析 `SKILL.md` |
| 配置加载 | `src/vico/config/loader.py` | 发现 `.vicorc.json` / `.env` 并构建 `AgentConfig` |

---

## 设计决策记录（ADR）

### ADR-001：为什么不使用独立 Planner LLM？

**决策**：当前实现只使用一个 Agent Loop，通过系统提示词要求模型在复杂任务前输出 `<plan>`。

**理由**：
1. 架构更简单：少一次 LLM 调用，也少一套 Planner 专用 Prompt 和回调。
2. 行为更直接：同一个模型既产出计划，也在同一上下文中继续执行。
3. 成本更低：避免为每个复杂任务额外支付一次规划请求。

### ADR-002：为什么自动批准工具并发，需审批工具串行？

**决策**：自动批准批次用 `asyncio.gather`；只要任一工具需要审批，整个批次串行执行。

**理由**：
1. 自动批准工具没有 UI 阻塞，可以并发提升速度。
2. 审批弹框需要稳定终端状态，串行更可控。
3. 单个工具失败通过 `return_exceptions=True` 隔离，不影响其他并发工具结果回传。

### ADR-003：为什么 Skill 正文懒加载？

**决策**：启动时只注入 Skill 元数据，触发后再把正文注入上下文。

**理由**：
1. 控制系统提示词长度。
2. 让模型先基于 description 判断是否需要 Skill。
3. 用户仍可通过 `/skill <id>` 显式覆盖模型选择。

### ADR-004：为什么只支持 `.vico` Skill 路径？

**决策**：当前只扫描 `<cwd>/.vico/skills` 和 `~/.vico/skills`。

**理由**：
1. 统一 Vico 自有配置入口。
2. 避免跨工具目录带来的优先级和语义歧义。
3. 后续若要兼容其他生态路径，应作为显式产品决策和代码变更处理。
