# Vico AI Agent — 核心架构设计

> 本文档描述 Vico Agent 的决策模型、规划机制与任务执行架构。
> 这是项目最重要的设计文档，理解它等于理解整个 Agent 的大脑。

---

## 目录

1. [设计哲学](#设计哲学)
2. [问题背景：为什么需要两阶段架构](#问题背景)
3. [总体架构：Plan → Execute → Observe](#总体架构)
4. [Phase 0：复杂度检测](#phase-0-复杂度检测)
5. [Phase 1：规划阶段（Planner）](#phase-1-规划阶段)
6. [Phase 2：执行阶段（Executor）](#phase-2-执行阶段)
7. [批量工具调用机制](#批量工具调用机制)
8. [上下文注入与信息传递](#上下文注入与信息传递)
9. [配置参考](#配置参考)
10. [代码索引](#代码索引)

---

## 设计哲学

Vico 的核心设计目标只有一句话：

> **用尽可能少的 LLM round-trips，完成尽可能复杂的任务。**

这对应两个正交的优化方向：

| 维度 | 问题 | 解法 |
|------|------|------|
| **广度** | 每次 LLM 调用只发出一个工具 | 批量工具调用（同一轮次并发） |
| **深度** | 没有全局计划，走一步看一步 | 前置规划阶段（一次拆解，全程导航） |

---

## 问题背景

### 🔴 原始问题：逐步思考模式（O(N) round-trips）

以"给电脑做体检"为例，原始 ReAct 模式的执行路径：

```
Turn 1:  LLM 思考 → 调用 bash("sw_vers")
Turn 2:  LLM 思考 → 调用 bash("sysctl -n machdep.cpu.brand_string")
Turn 3:  LLM 思考 → 调用 bash("df -h")
Turn 4:  LLM 思考 → 调用 bash("netstat ...")
Turn 5:  LLM 思考 → 调用 bash("ifconfig ...")
Turn 6:  LLM 思考 → 调用 bash("pmset -g batt")
...（共 14 次 LLM 调用）
```

**问题所在：**
- 每次 LLM 调用都携带完整的上下文历史（随着工具结果累积，token 消耗线性增长）
- 绝大多数工具调用之间没有依赖关系，却被串行执行
- 没有全局规划，执行路径不可预测，容易遗漏检查项

### ✅ 优化目标：两阶段模式（接近 O(log N) round-trips）

```
Turn 0 (Planning):  LLM 分析任务 → 输出结构化 plan
Turn 1 (Execute):   LLM 读 plan → 同时输出 6 个 bash 工具调用（并发执行）
Turn 2 (Execute):   LLM 读结果 → 同时输出 3 个 bash 工具调用（并发执行）
Turn 3 (Finalize):  LLM 综合所有结果 → 输出最终报告
```

---

## 总体架构

```
用户输入
   │
   ▼
┌─────────────────────────────────────────────────────────────────┐
│                        AgentLoop.run()                          │
│                                                                 │
│  ① _is_complex_task()                                          │
│      │                                                          │
│      ├─ 简单任务 ────────────────────────────────────►  ③      │
│      │                                                          │
│      └─ 复杂任务                                                │
│            │                                                    │
│            ▼                                                    │
│  ② _run_planning_phase()                                       │
│      ┌──────────────────────┐                                  │
│      │   Planner LLM 调用   │  ← 无工具权限，仅输出 <plan>      │
│      │   (max_tokens=2048)  │                                  │
│      └──────────────────────┘                                  │
│            │                                                    │
│            ▼                                                    │
│      注入 <plan_summary> 到对话上下文                           │
│      触发 on_plan() → 渲染 Plan 面板                            │
│            │                                                    │
│            ▼                                                    │
│  ③ _loop()  ←──────────────────────────────────────────────   │
│      │                                                          │
│      ├─ Executor LLM 调用（携带 plan 上下文 + 工具定义）        │
│      │       │                                                  │
│      │       └─ 输出多个 ToolCall（[batch] 步骤）               │
│      │                                                          │
│      ├─ asyncio.gather(*tool_calls)  ← 并发执行所有工具          │
│      │                                                          │
│      ├─ 收集结果 → 追加到上下文                                  │
│      │                                                          │
│      └─ 有更多工具调用? ──YES──► 回到 Executor LLM              │
│                  │                                              │
│                  NO                                             │
│                  ▼                                              │
│            输出最终回复                                          │
└─────────────────────────────────────────────────────────────────┘
```

---

## Phase 0：复杂度检测

**入口**：`AgentLoop._is_complex_task(user_input: str) -> bool`

**逻辑**（满足任一即为复杂任务）：

```python
# 1. 关键词快速匹配
for kw in config.planning.complexity_keywords:
    if kw.lower() in text_lower:
        return True   # 立即触发规划

# 2. 词数阈值（剥离标点后统计）
words = re.findall(r"\w+", user_input)
return len(words) >= config.planning.min_words
```

**内置复杂性关键词分类：**

| 类别 | 关键词 |
|------|--------|
| 系统诊断 | `体检`, `检查`, `诊断`, `health`, `diagnos`, `inspect` |
| 多步任务 | `重构`, `refactor`, `migrate`, `迁移`, `设计`, `design` |
| 宽泛范围 | `全面`, `all`, `每个`, `全部`, `comprehensive`, `complete` |
| 调试排查 | `debug`, `分析`, `investigate`, `排查`, `troubleshoot` |
| 配置部署 | `setup`, `install`, `配置`, `搭建`, `部署`, `deploy` |

> **短命令直通**：`ls`、`cat README.md`、`帮我看看这个文件` 等简单请求直接跳过规划阶段，不引入额外延迟。

---

## Phase 1：规划阶段

**入口**：`AgentLoop._run_planning_phase(user_input: str) -> str | None`

### Planner 的特殊约束

```
系统 Prompt:  build_planner_prompt()        ← 专用 Prompt
工具定义:     tools=None                    ← ⚠️ 不传工具，无法调用任何工具
max_tokens:   2048                          ← 比执行阶段小得多，控制规划成本
```

> **关键设计**：Planner 没有工具权限，它**只能写文字**。这保证了规划阶段不会触发任何实际操作，也不会产生权限确认弹框，是纯粹的"思考"环节。

### Plan 格式规范

Planner 被要求严格输出如下格式：

```
<plan>
Goal: <一句话描述目标>
Steps:
  1. [batch] bash: <命令1>  +  bash: <命令2>  +  bash: <命令3>
  2. [seq]   read: <文件路径>  →  edit: <文件路径>
  3. [batch] bash: <验证1>  +  bash: <验证2>
Safety: <风险说明，或 "none">
</plan>
```

**步骤标注含义：**

| 标注 | 含义 | Agent 行为 |
|------|------|------------|
| `[batch]` | 工具间无输出依赖，可并行 | 同一 LLM 轮次输出，`asyncio.gather` 并发执行 |
| `[seq]` | 下一步依赖上一步的输出 | 分两次 LLM 轮次，顺序执行 |

### Planner Prompt 的核心指令

```
你的唯一职责是分析用户请求，产出结构化执行计划。
你没有任何工具——不要尝试调用任何函数或输出工具调用。
你的输出是一个 <plan> 块，供 Executor 用来批量调度工具调用。
```

### Plan 展示

规划完成后，Plan 文本通过 `on_plan()` 回调传给渲染器，在终端以 Rich Panel 面板形式展示：

```
╭─ 📋 Plan ──────────────────────────────────────────────────╮
│ Goal: Comprehensive system health check                    │
│ Steps:                                                     │
│   1. [batch] bash: sw_vers + bash: sysctl ... + bash: df  │
│   2. [batch] bash: netstat + bash: ifconfig               │
│   3. [batch] bash: pmset + bash: log show ...             │
│ Safety: none                                               │
╰────────────────────────────────────────────────────────────╯
```

---

## Phase 2：执行阶段

**入口**：`AgentLoop._loop(max_iterations: int)`

### Executor 的工作流

```
for iteration in range(max_iterations):
    │
    ├─ Step 1: 调用 LLM（携带完整上下文 + 工具定义 + plan_summary）
    │              │
    │              ├─ 流式接收: TextChunk → 积累文本
    │              ├─ 流式接收: ReasoningChunk → on_thinking() 展示
    │              ├─ 流式接收: ToolCallChunk → 加入 pending_tool_calls[]
    │              └─ 流式接收: DoneChunk → 更新 token 统计
    │
    ├─ Step 2: 保存 assistant 消息到上下文
    │
    ├─ Step 3: 如果没有工具调用 → break（任务完成）
    │
    ├─ Step 4: 执行工具调用
    │   │
    │   ├─ 需要审批? → 串行执行（逐个弹确认框）
    │   │
    │   └─ 全部自动批准? → asyncio.gather() 并发执行
    │                          ↑
    │                   ⭐ 这是批量执行的核心
    │
    └─ Step 5: 追加工具结果到上下文 → 继续循环
```

### Executor Prompt 的核心约束

```
## 🚀 Batch Tool Calls (CRITICAL — minimise LLM round-trips)

规则：
- 如果多个工具调用之间没有输出依赖，在同一轮次输出所有工具调用
- 永远不要在可以并行时逐个调用工具
- 只有当 N 步的输出是 N+1 步的输入时，才允许串行

启发式判断：
  "如果我现在就能写出所有命令，不需要等待任何结果，就批量发出。"
```

---

## 批量工具调用机制

### 底层实现

`AgentLoop` 对多个并发工具调用的处理：

```python
# 判断是否需要审批
needs_approval = any(
    not self._permissions.is_auto_approved(tc, self._tool_registry)
    for tc in pending_tool_calls
)

if needs_approval:
    # 串行执行（逐个显示确认框）
    for tc in pending_tool_calls:
        result = await self._execute_one(tc)

else:
    # 并发执行（asyncio.gather）
    raw_results = await asyncio.gather(
        *[self._execute_one(tc) for tc in pending_tool_calls],
        return_exceptions=True,   # 单个工具失败不影响其他工具
    )
```

### 并发 vs 串行决策树

```
pending_tool_calls
       │
       ▼
  任意一个需要审批?
       │
   YES ┤                     NO
       ▼                      ▼
  串行执行              asyncio.gather 并发执行
  （保护终端 UI）        （最大化吞吐）
```

### 效率对比

| 场景 | 原始（串行） | 优化后（批量并发） |
|------|-------------|-----------------|
| 电脑体检（14 个 bash） | 14 次 LLM 调用 | ~3 次 LLM 调用 |
| 代码重构（read+搜索+edit+验证） | 6 次 LLM 调用 | ~2 次 LLM 调用 |
| 简单查询（1 个工具） | 1 次 LLM 调用 | 1 次 LLM 调用（无变化） |

---

## 上下文注入与信息传递

### Plan 如何传递给 Executor

规划完成后，Plan 文本以 `assistant` 消息的形式注入到对话历史：

```python
self._context.add_assistant_message(
    text=f"<plan_summary>\n{plan_note}\n</plan_summary>",
    tool_calls=None,
)
```

Executor 在下一次 LLM 调用时，`messages` 中已包含这条 plan_summary，模型会自然地按照计划执行。

### 信息流全景

```
用户消息
   │
   ▼
[上下文: user_message]
   │
   ▼ (复杂任务)
Planner LLM → plan 文本
   │
   ▼
[上下文: user_message, assistant(plan_summary)]
   │
   ▼
Executor LLM (Turn 1)
   → 输出 ToolCall × N
   │
   ▼
[上下文: ..., assistant(tool_calls), tool_result × N]
   │
   ▼
Executor LLM (Turn 2)  ← 继续按 plan 执行
   → 输出 ToolCall × M
   │
   ▼
... 直到无工具调用
   │
   ▼
[上下文: ..., assistant(最终回复)]
```

---

## 配置参考

### `PlanningConfig`（`src/vico/core/types.py`）

```python
@dataclass
class PlanningConfig:
    enabled: bool = True              # Planning Phase 总开关
    min_words: int = 8                # 触发规划的最低词数
    complexity_keywords: list[str]    # 触发规划的关键词（内置 + 用户追加）
```

### `.vicorc.json` 配置项

```jsonc
{
  "planning": {
    "enabled": true,
    "min_words": 8,
    "complexity_keywords": ["your-keyword"]  // 追加到内置列表，不覆盖
  }
}
```

### 关闭规划（适用于速度敏感场景）

```jsonc
{
  "planning": {
    "enabled": false
  }
}
```

---

## 代码索引

| 模块 | 文件 | 职责 |
|------|------|------|
| 复杂度检测 | `src/vico/core/agent_loop.py` → `_is_complex_task()` | 判断是否触发规划 |
| 规划执行 | `src/vico/core/agent_loop.py` → `_run_planning_phase()` | 调用 Planner LLM |
| 主循环 | `src/vico/core/agent_loop.py` → `_loop()` | Executor 循环 + 并发工具调用 |
| Executor Prompt | `src/vico/core/system_prompt.py` → `build_system_prompt()` | 含批量调用规范 |
| Planner Prompt | `src/vico/core/system_prompt.py` → `build_planner_prompt()` | 专用无工具 Prompt |
| 配置类型 | `src/vico/core/types.py` → `PlanningConfig` | 规划配置数据类 |
| 配置解析 | `src/vico/config.py` → `_parse_planning_config()` | 读取 .vicorc.json |
| Plan 渲染 | `src/vico/cli/renderer.py` → `on_plan()` | 终端 Plan 面板展示 |
| 回调注册 | `src/vico/cli/__init__.py` → `AgentCallbacks` | on_plan 回调接入 |

---

## 设计决策记录（ADR）

### ADR-001：为什么 Planner 不传工具定义？

**决策**：Planner 调用 `LLMRequest(tools=None)`。

**理由**：
1. **防止误触发**：如果 Planner 有工具权限，模型可能在规划阶段就开始执行工具
2. **降低成本**：规划阶段 max_tokens=2048，成本极低；工具定义本身消耗大量 token
3. **职责分离**：Planner 只思考"做什么"，Executor 才负责"怎么做"

### ADR-002：为什么 Plan 以 assistant 消息注入而非 system 消息？

**决策**：`context.add_assistant_message(text=f"<plan_summary>...")`

**理由**：
1. **符合对话流**：Plan 是 Agent 自己产出的，放在 assistant 角色下语义正确
2. **可见性**：Executor 看到上文有 plan，自然地把它作为执行指南
3. **兼容压缩**：当上下文接近限制触发压缩时，plan_summary 和其他消息一样被处理

### ADR-003：为什么非规划失败不影响执行？

**决策**：`_run_planning_phase` 内部捕获 `ErrorChunk`，失败时返回 `None`，流程继续。

**理由**：
1. **鲁棒性优先**：规划是锦上添花，不应成为单点故障
2. **降级兼容**：没有 Plan，Executor 仍能正常运行（只是 round-trips 可能更多）
