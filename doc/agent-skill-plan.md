# Vico Agent Skill 支持能力接入计划

> 版本：v1.0  
> 日期：2026-06-09  
> 作者：基于 Claude Code、OpenAI Agents SDK、Gemini CLI 竞品调研综合分析

---

## 目录

1. [竞品调研：主流 Agent 的 Skill 能力](#一竞品调研)
2. [Vico 现状分析](#二vico-现状分析)
3. [产品形态设计：Vico Skill 规格定义](#三产品形态设计)
4. [技术架构方案](#四技术架构方案)
5. [实现路线图](#五实现路线图)
6. [配置参考与示例](#六配置参考与示例)
7. [设计决策记录（ADR）](#七设计决策记录)

---

## 一、竞品调研

### 1.1 Claude Code — Skills + Hooks 体系

#### Skill 机制
Claude Code 的 Skill 是业界最完整的 Agent 扩展规范，基于开放的 [agentskills.io](https://agentskills.io) 标准：

- **存储结构**：每个 Skill 是目录，核心文件为 `SKILL.md`，支持附带 templates/、scripts/、examples/ 子目录
- **层级优先级**：`enterprise > user (~/.claude/skills/) > project (.claude/skills/)  > plugin`
- **Frontmatter 元数据**：`name`、`description`、`when_to_use`、`argument-hint`、`disable-model-invocation`、`user-invocable`、`allowed-tools`、`disallowed-tools`、`model`、`effort`、`context`（fork/subagent 隔离）、`hooks`、`paths`
- **调用控制**：
  - 用户手动触发：`/skill-name`
  - Claude 自动激活：依据 `description` 语义匹配
  - `disable-model-invocation: true`：只允许用户手动触发（适合有副作用的操作）
  - `user-invocable: false`：从菜单隐藏，只允许 Claude 调用（适合背景知识型 Skill）
- **动态上下文注入**：`` !`command` `` 语法，在 Skill 加载时执行 shell 命令并将输出内联注入
- **变量替换**：`$ARGUMENTS`、`$ARGUMENTS[N]`、具名参数 `$name`、`${CLAUDE_SKILL_DIR}`
- **上下文开销**：Skill 描述在会话开始时注入，Skill 正文仅在使用时加载（懒加载）

#### Hooks 机制
27 种生命周期事件（`PreToolUse`、`PostToolUse`、`SessionStart`、`Stop` 等），5 种 Hook 类型（command、http、mcp_tool、prompt、agent）。

**关键洞察**：Custom Commands（`.claude/commands/`）已完全合并进 Skills 机制——旧格式继续有效，新 Skill 格式提供更强能力。

---

### 1.2 OpenAI Agents SDK / Codex — Skills as Versioned Bundles

OpenAI 的 Skill 是版本化可分发文件包（兼容 agentskills.io 标准）：

- **SKILL.md 格式**：`name`、`description` frontmatter + Markdown 正文
- **两种执行模式**：
  - Hosted Shell（托管容器）：通过 `skill_reference` + `skill_id` 引用；支持版本锁定
  - Local Shell（本地）：从本地路径提供 Skill 文件
- **API 管理**：REST API 支持 multipart 或 zip 上传；支持版本管理（`default_version`、`latest_version`）
- **策划技能（Curated Skills）**：平台官方维护的第一方 Skill（如 `openai-spreadsheets`）
- **内联 Skill**：base64 zip 格式，无需上传即可使用
- **Prompting 行为**：Skill 元数据自动注入用户 prompt 上下文，模型自主决策是否调用；正文属于用户提示，优先级等同于普通用户指令
- **限制**：50MB/zip、500文件/版本、25MB/单文件

**关键洞察**：OpenAI 强调 Skill 的版本化和托管分发能力，适合企业级工作流沉淀与复用。

---

### 1.3 Gemini CLI — Extensions + Skills + Hooks 三层体系

Gemini CLI 的扩展架构分三层：

#### Agent Skills
- **标准**：遵循 agentskills.io 开放标准
- **发现机制**：会话开始时扫描所有 Skill 目录，将 name+description 注入 system prompt
- **激活流程**：模型调用内置 `activate_skill` 工具 → 用户 UI 确认 → `SKILL.md` 内容注入对话历史 → 执行
- **层级**：内置技能 → 扩展技能 → 用户技能（`~/.gemini/skills/`）→ 工作区技能（`.gemini/skills/`）
- **别名路径**：`.agents/skills/`（跨 AI 工具兼容路径）

#### Custom Commands（TOML 格式）
- 路径：`~/.gemini/commands/` 或 `.gemini/commands/`
- 格式：`description` + `prompt` 字段，支持 `{{args}}` 参数占位符
- 命名空间：目录结构对应命名空间（`git/commit.toml` → `/git:commit`）
- 动态内容：`!{shell_command}` 语法执行 shell 命令注入输出

#### Hooks（11 种生命周期事件）
`SessionStart`、`BeforeAgent`、`AfterAgent`、`BeforeModel`、`AfterModel`、`BeforeToolSelection`、`BeforeTool`、`AfterTool`、`PreCompress`、`SessionEnd`、`Notification`

**关键洞察**：Gemini CLI 的 `activate_skill` 内置工具 + 用户确认流程，是保护上下文窗口的精妙设计——Skill 内容不在会话开始时全量注入，仅在用户确认后按需加载。

---

### 1.4 竞品对比总表

| 维度 | Claude Code | OpenAI/Codex | Gemini CLI |
|------|------------|--------------|------------|
| **Skill 文件格式** | SKILL.md (YAML frontmatter + MD) | SKILL.md (YAML + MD) | SKILL.md (同上) |
| **标准** | agentskills.io + 扩展 | agentskills.io | agentskills.io |
| **调用方式** | /命令 + 自动激活 | 提示上下文自动 | activate_skill 工具 + 用户确认 |
| **层级系统** | enterprise/user/project/plugin | 无层级（API管理） | 内置/扩展/用户/工作区 |
| **版本管理** | 无 | 完整版本 API | 无 |
| **上下文策略** | 描述先加载，正文懒加载 | 元数据注入 prompt | 确认后才加载正文 |
| **Hooks** | 27 种事件，5 种类型 | N/A | 11 种事件 |
| **Custom Commands** | 已合并进 Skill | N/A | 独立 TOML 格式 |
| **动态上下文** | `!`cmd`` 内联执行 | 无 | `!{cmd}` 内联执行 |
| **子目录支持** | templates/scripts/examples/ | zip包文件树 | 任意文件 |

**核心共识**：三大平台均收敛到 `agentskills.io` 标准，SKILL.md 是跨平台通用格式；上下文管理（懒加载/按需激活）是核心设计约束。

---

## 二、Vico 现状分析

### 2.1 现有能力盘点

| 组件 | 文件 | 当前状态 |
|------|------|---------|
| 工具注册 | `tools/registry.py` | ✅ 完整，支持动态注册 |
| 系统提示词 | `core/system_prompt.py` | ✅ Jinja2 模板，支持 `_resolve_prompt_file` 多级覆盖 |
| 提示词加载 | `core/prompt_loader.py` | ✅ FileSystemLoader，支持 `{% include %}` |
| Agent 循环 | `core/agent_loop.py` | ✅ think→act→observe，支持并发工具调用 |
| 配置读取 | `config.py` | ✅ `.vicorc.json` + `.env`，多级查找 |
| CLI 命令 | `cli/commands.py`, `cli/repl.py` | ✅ `/clear`、`/model`、`/help`、`/exit`、`/skills`、`/skill` |
| 权限控制 | `core/permission_controller.py` | ✅ 风险级别自动审批 + 会话审批 |
| 人物角色 | `prompts/Vico.md`, `prompts/User.md` | ✅ 支持 `<cwd>/.vico/` → `~/.vico/` → 默认三级覆盖 |
| Skill 扫描 | `skills/loader.py` | ✅ 支持 `<cwd>/.vico/skills` 与 `~/.vico/skills` |
| Skill 激活 | `tools/activate_skill.py` | ✅ 支持结构化 `activate_skill` 工具，保留 `<use_skill>` 兼容后备 |

### 2.2 现有能力与 Skill 的剩余缺口

| 剩余缺口 | 影响 |
|---------|------|
| 无动态命令注入 | SKILL.md 中的动态上下文命令不会自动执行并内联 |
| 无 Custom Commands | 无法用 `/cmd` 快捷触发 Prompt 模板 |
| 无 Hooks 机制 | 无法在生命周期节点拦截执行 |
| `allowed-tools` 仅解析展示 | 尚未按 Skill 临时调整工具权限 |

### 2.3 架构优势（可利用的基础设施）

1. **`_resolve_prompt_file`**：已有三级路径查找逻辑（`<cwd>/.vico/` → `~/.vico/` → 默认），Skill 目录查找可复用此模式
2. **`ToolRegistry.register()`**：支持运行时注册工具，`activate_skill` 工具可作为内置工具注入
3. **Jinja2 FileSystemLoader**：支持 `{% include %}` 和变量替换，Skill 内容注入系统提示词极其自然
4. **AgentCallbacks**：完整的回调体系，可添加 `on_skill_activated` 回调
5. **REPL 命令解析**：已有 `/xxx` 命令处理框架，新增 `/skills` 命令成本极低

---

## 三、产品形态设计

### 3.1 Vico Skill 规格

综合三大竞品的最佳实践，结合 Vico 的 Python/CLI 定位，定义 Vico Skill 规格如下：

#### 文件格式：SKILL.md

```markdown
---
name: my-skill-display-name        # 显示名称（必须）
description: |                      # 描述（必须，用于模型发现）
  When to use this skill and what it does in 1-3 sentences.
  The model uses this to decide whether to activate the skill.
argument-hint: "[task-description]" # 可选，参数提示
disable-model-invocation: false     # 可选，禁止模型自动激活
user-invocable: true                # 可选，是否在 /skills list 中显示
allowed-tools: ["bash", "read"]     # 可选，激活时额外允许的工具
risk-level: "medium"                # 可选，激活本 Skill 的整体风险评估
---

# Skill Instructions

The Markdown body here is the full skill instructions injected into 
the conversation when this skill is activated.

## Variables
- `$ARGUMENTS` — arguments passed by the user
- `${VICO_SKILL_DIR}` — absolute path to this skill's directory
- `${VICO_CWD}` — current working directory

## Dynamic Context (shell inline)
!`git log --oneline -10`
```

#### 目录结构

```
my-skill/
├── SKILL.md          # 主指令文件（必须）
├── templates/        # 模板文件（可选）
│   └── template.md
├── scripts/          # 可执行脚本（可选）
│   └── validate.sh
└── examples/         # 示例输出（可选）
    └── sample.md
```

#### 存储路径（优先级从高到低）

| 优先级 | 路径 | 作用范围 |
|--------|------|---------|
| 1 | `<cwd>/.vico/skills/<skill-name>/` | 项目级，可纳入版本控制 |
| 2 | `~/.vico/skills/<skill-name>/` | 用户级，全局可用 |
| 3 | 内置 Skill（Vico 官方打包） | 默认能力 |

> **当前项目口径**：Vico 只从 `.vico/skills/` 和 `~/.vico/skills/` 扫描 Skill；暂不支持 `.agents/skills/` 兼容路径。

### 3.2 调用方式

#### 方式一：用户手动触发（CLI 命令）

```bash
# 列出所有可用 Skill
/skills

# 激活指定 Skill（可选带参数）
/skill code-review
/skill security-audit my-feature-branch

# 等效的自然语言触发
用 code-review skill 帮我看看这个 PR
```

#### 方式二：模型自动激活

模型在每次 LLM 调用时，从 system prompt 的 `## Available Skills` 区块看到所有 Skill 的 name+description。当判断当前任务与某个 Skill 匹配时，模型调用内置 `activate_skill` 工具：

```json
{
  "tool": "activate_skill",
  "input": {
    "skill_name": "security-audit",
    "reason": "User is asking for a security review of the codebase"
  }
}
```

激活流程：
1. `activate_skill` 工具接收 `skill_name`
2. 找到对应 SKILL.md，解析 frontmatter
3. 若 `disable-model-invocation: true`，拒绝并通知用户手动触发
4. 否则，将 Skill 正文（执行动态命令后）注入对话上下文
5. 调用 `on_skill_activated` 回调，渲染器显示 Skill 激活面板
6. Agent 继续下一轮 LLM 调用，此时上下文已含 Skill 内容

#### 方式三：Skill 作为 Custom Command（快捷命令）

在 `SKILL.md` frontmatter 中指定 `command: true`，则该 Skill 同时注册为 `/skill-name` 命令：

```yaml
---
name: deploy
description: Deploy the current branch to staging
command: true           # 注册为 /deploy 命令
disable-model-invocation: true  # 只允许用户手动触发
---
```

### 3.3 上下文管理策略

借鉴 Gemini CLI 的"确认后才加载正文"策略，Vico 采用**分阶段注入**：

| 阶段 | 注入内容 | 注入时机 |
|------|---------|---------|
| 会话启动 | 所有 Skill 的 `name + description`（摘要） | `build_system_prompt()` 时 |
| Skill 激活后 | Skill 的完整正文（含动态命令输出） | `activate_skill` 工具执行后 |

这样的好处：
- 会话启动时 token 开销极小（仅描述字符串）
- Skill 正文按需注入，不浪费上下文窗口
- 同时支持模型自主发现和用户手动触发两种路径

---

## 四、技术架构方案

### 4.1 新增模块概览

```
src/vico/
├── skills/                        # 新增模块
│   ├── __init__.py
│   ├── loader.py                  # SkillLoader：扫描/解析/缓存 Skill
│   ├── models.py                  # SkillMeta、SkillContent 数据类
│   └── activate_tool.py           # ActivateSkillTool：内置工具
├── core/
│   ├── system_prompt.py           # 改造：注入 Skill 摘要到 system prompt
│   └── agent_loop.py              # 改造：新增 on_skill_activated 回调
├── cli/
│   └── __init__.py                # 改造：新增 /skills 命令处理
└── prompts/
    └── skills.md                  # 新增：Skill 摘要的 Jinja2 模板片段
```

### 4.2 核心数据类型

```python
# src/vico/skills/models.py

from dataclasses import dataclass, field
from pathlib import Path

@dataclass
class SkillMeta:
    """Parsed from SKILL.md frontmatter."""
    name: str                           # 显示名称
    description: str                    # 用于模型发现的描述（1-3句）
    argument_hint: str = ""             # 参数提示
    disable_model_invocation: bool = False  # 禁止模型自动激活
    user_invocable: bool = True         # 是否显示在 /skills 列表
    allowed_tools: list[str] = field(default_factory=list)
    risk_level: str = "medium"
    skill_dir: Path = field(default_factory=Path)
    skill_id: str = ""                  # 唯一标识符（目录名）


@dataclass
class SkillContent:
    """Skill full content, loaded on demand."""
    meta: SkillMeta
    body: str           # SKILL.md body (below frontmatter)
    rendered_body: str  # body after dynamic command execution
```

### 4.3 SkillLoader — 核心扫描与解析器

```python
# src/vico/skills/loader.py

class SkillLoader:
    """
    扫描所有 Skill 目录，解析 SKILL.md，提供 Skill 发现和内容加载接口。

    搜索路径（优先级从高到低）:
      1. <cwd>/.vico/skills/
      2. ~/.vico/skills/

    同名 Skill 按优先级覆盖（高优先级遮蔽低优先级）。
    """

    def __init__(self, cwd: str) -> None:
        self._cwd = Path(cwd)
        self._skills: dict[str, SkillMeta] = {}  # skill_id → SkillMeta
        self._scan()

    def get_all_metas(self) -> list[SkillMeta]:
        """返回所有 Skill 的元数据列表（用于 system prompt 注入）"""

    def get_skill_content(self, skill_id: str) -> SkillContent | None:
        """按需加载并渲染 Skill 完整内容（含动态命令执行）"""

    def _scan(self) -> None:
        """扫描所有 Skill 目录，填充 self._skills"""

    def _parse_skill_md(self, path: Path) -> SkillMeta | None:
        """解析 SKILL.md，提取 frontmatter 和 body"""

    def _execute_dynamic_commands(self, body: str, skill_dir: Path, cwd: str) -> str:
        """执行 body 中的 !`command` 内联命令，将输出替换回 body"""
```

### 4.4 ActivateSkillTool — 内置工具

```python
# src/vico/skills/activate_tool.py

class ActivateSkillTool(Tool):
    """
    内置工具：activate_skill
    
    模型调用此工具触发 Skill 激活流程。该工具：
    1. 验证 Skill 存在
    2. 检查 disable-model-invocation 标志
    3. 加载并渲染 Skill 完整内容
    4. 触发 on_skill_activated 回调（渲染器显示面板）
    5. 将 Skill 内容注入上下文（返回给 AgentLoop）
    
    工具参数：
      skill_name: str — Skill 的 ID（目录名）或 name
      reason: str — 模型激活该 Skill 的理由（用于展示给用户）
      arguments: str — 传递给 Skill 的参数（替换 $ARGUMENTS）
    
    风险级别：low（元数据查询）/ 由 Skill 的 risk-level 决定
    """

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="activate_skill",
            description=(
                "Activate a Vico skill to load specialized instructions for the current task. "
                "Use when you identify that a skill matches the user's request."
            ),
            parameters=ToolParameterSchema(
                type="object",
                properties={
                    "skill_name": {"type": "string", "description": "Skill ID or display name"},
                    "reason": {"type": "string", "description": "Why you are activating this skill"},
                    "arguments": {"type": "string", "description": "Arguments to pass to the skill"},
                },
                required=["skill_name", "reason"],
            ),
        )
```

### 4.5 system_prompt.py 改造

```python
# 在 build_system_prompt() 中增加 Skill 摘要注入

def build_system_prompt(cwd: str) -> str:
    variables = _make_variables(cwd)
    loader = get_loader()
    skill_loader = SkillLoader(cwd)   # 新增

    variables["vico_content"] = _resolve_prompt_file(...)
    variables["user_content"] = _resolve_prompt_file(...)

    # 新增：注入 Skill 摘要（所有 Skill 的 name + description）
    skills_summary = _build_skills_summary(skill_loader.get_all_metas())
    variables["skills_summary"] = skills_summary

    prompt = loader.render(variables)
    loader.check_token_budget(prompt)
    return prompt.strip()


def _build_skills_summary(metas: list[SkillMeta]) -> str:
    """
    生成注入 system prompt 的 Skill 摘要块。
    
    格式示例：
    ## Available Skills
    You have access to the following skills. Use `activate_skill` tool to activate one
    when it matches the user's request.
    
    - **code-review** (code-review): Review code for bugs, security issues, and style
    - **security-audit** (security-audit): Perform comprehensive security audit of codebase
    """
    if not metas:
        return ""
    
    lines = [
        "## Available Skills",
        "You have the following skills available. Call `activate_skill` when a skill",
        "matches the user's task to load its full instructions.",
        "",
    ]
    for meta in metas:
        if meta.user_invocable:
            prefix = f"- **{meta.skill_id}**"
            hint = f" {meta.argument_hint}" if meta.argument_hint else ""
            lines.append(f"{prefix}{hint}: {meta.description.splitlines()[0]}")
    return "\n".join(lines)
```

### 4.6 Agent.md 模板改造

```markdown
<!-- 在 Agent.md 末尾新增 Skills 区块 -->

{% if skills_summary %}
{{ skills_summary }}
{% endif %}
```

### 4.7 AgentLoop 改造

```python
# 新增回调类型
OnSkillActivatedCallback = Callable[[SkillMeta, str], None]
# skill_meta: 被激活的 Skill；injected_content: 注入的内容摘要

@dataclass
class AgentCallbacks:
    # ... 现有回调 ...
    on_skill_activated: OnSkillActivatedCallback | None = None  # 新增

class AgentLoop:
    def __init__(self, ..., skill_loader: SkillLoader | None = None) -> None:
        # 新增参数
        self._skill_loader = skill_loader
        # 如果有 SkillLoader，注册 activate_skill 工具
        if skill_loader:
            self._tool_registry.register(
                ActivateSkillTool(
                    skill_loader=skill_loader,
                    context_injector=self._inject_skill_content,
                    on_activated=callbacks.on_skill_activated,
                )
            )

    def _inject_skill_content(self, content: SkillContent) -> None:
        """将激活的 Skill 内容注入对话上下文（user 消息形式）。"""
        self._context.add_user_message(
            f"<skill_activated name=\"{content.meta.skill_id}\">\n"
            f"{content.rendered_body}\n"
            f"</skill_activated>"
        )
```

### 4.8 CLI 改造：新增 /skills 命令

```python
# 在 repl() 的命令处理区块新增

if user_input == "/skills":
    _handle_skills_command(skill_loader, renderer)
    continue

if user_input.startswith("/skill "):
    skill_arg = user_input[7:].strip()
    _handle_activate_skill_command(skill_arg, agent, skill_loader, renderer)
    continue

# 处理函数

def _handle_skills_command(skill_loader: SkillLoader, renderer: TerminalRenderer) -> None:
    """列出所有可用的 Skill。"""
    metas = [m for m in skill_loader.get_all_metas() if m.user_invocable]
    if not metas:
        console.print("  [dim]No skills found. Place SKILL.md files in .vico/skills/<name>/[/dim]")
        return
    console.print("\n  [bold]Available Skills[/bold]")
    for meta in metas:
        hint = f" [dim]{meta.argument_hint}[/dim]" if meta.argument_hint else ""
        console.print(f"  [cyan]/skill {meta.skill_id}{hint}[/cyan]  {meta.description.splitlines()[0]}")
    console.print()

async def _handle_activate_skill_command(
    skill_arg: str,
    agent: AgentLoop,
    skill_loader: SkillLoader,
    renderer: TerminalRenderer,
) -> None:
    """用户手动 /skill <name> [args] 激活某个 Skill。"""
    parts = skill_arg.split(maxsplit=1)
    skill_id = parts[0]
    arguments = parts[1] if len(parts) > 1 else ""
    
    content = skill_loader.get_skill_content(skill_id)
    if not content:
        console.print(f"  [red]✗[/red]  Skill not found: '{skill_id}'")
        return
    
    # 直接注入（用户手动触发，不受 disable-model-invocation 限制）
    rendered = _render_skill_body(content, arguments)
    agent._inject_skill_content(rendered)
    renderer.on_skill_activated(content.meta, len(rendered))
    console.print(f"  [green]✓[/green]  Skill '[cyan]{skill_id}[/cyan]' activated.")
```

### 4.9 渲染器改造：TerminalRenderer.on_skill_activated

```python
def on_skill_activated(self, meta: SkillMeta, content_len: int) -> None:
    """展示 Skill 激活面板。"""
    from rich.panel import Panel
    console.print(
        Panel(
            f"[bold]{meta.name}[/bold]\n"
            f"[dim]{meta.description.splitlines()[0]}[/dim]\n\n"
            f"[dim]Loaded {content_len} chars of skill instructions[/dim]",
            title="🎯 Skill Activated",
            border_style="cyan",
        )
    )
```

---

## 五、实现路线图

### Phase 1：基础 Skill 支持（MVP）— 已完成主体能力

**目标**：用户可以将 SKILL.md 放到 `.vico/skills/<name>/` 目录，Vico 自动发现并将摘要注入系统提示词，模型可以通过 `activate_skill` 工具激活 Skill。

| 任务 | 文件 | 复杂度 |
|------|------|--------|
| 创建 `SkillMeta`、`SkillContent` 数据类 | `src/vico/skills/types/meta.py` | 已完成 |
| 实现 `SkillLoader` 扫描与解析 | `src/vico/skills/loader.py` | 已完成 |
| 实现 `ActivateSkillTool` | `src/vico/tools/activate_skill.py` | 已完成 |
| 改造 `build_system_prompt()` 注入 Skill 摘要 | `core/system_prompt.py` | 已完成 |
| 改造 `Agent.md` 模板 | `prompts/Agent.md` | 已完成 |
| 注册 `activate_skill` 工具 | `cli/session.py` | 已完成 |
| 新增 `/skills` 命令 | `cli/commands.py` | 已完成 |
| 新增 `on_skill_activated` 渲染 | `cli/session.py` | 已完成 |
| 更新 `AgentCallbacks` | `core/types/callbacks.py` | 已完成 |

**验收标准**：
- [x] 在 `.vico/skills/code-review/SKILL.md` 放置 Skill 文件
- [x] `vico` 启动后，`/skills` 命令显示该 Skill
- [x] 告诉 Vico "帮我 review 这段代码"，模型可自动调用 `activate_skill`
- [x] 终端显示 Skill 激活提示，之后 Vico 使用 Skill 中的专业指令执行任务

---

### Phase 2：Skill 增强能力 — 部分完成

**目标**：支持动态命令注入、参数替换、变量替换、`/skill <name> [args]` 手动触发。

| 任务 | 文件 | 复杂度 |
|------|------|--------|
| 实现 `!`cmd`` 动态命令执行 | `skills/loader.py` | 中 |
| 实现 `$ARGUMENTS`、`${VICO_SKILL_DIR}`、`${VICO_CWD}` 变量替换 | `core/agent_loop.py` | 已完成 |
| 支持 `/skill <name> [args]` 手动触发命令 | `cli/commands.py` | 已完成 |
| 支持 `disable-model-invocation` frontmatter | `tools/activate_skill.py` | 已完成 |
| 支持 `user-invocable: false` frontmatter | `skills/loader.py` | 已完成 |
| 支持 `allowed-tools` frontmatter（临时扩展工具权限） | `core/permission_controller.py` | 未开始 |

**验收标准**：
- [ ] SKILL.md body 中的 `` !`git log --oneline -5` `` 在激活时自动执行并内联
- [x] `/skill deploy production` 将 `production` 作为 `$ARGUMENTS` 传入 Skill 正文
- [x] `disable-model-invocation: true` 的 Skill 模型无法自动激活，仅用户 `/skill` 可触发

---

### Phase 3：内置 Skill 库 — 预计 1-2 天

**目标**：提供官方打包的内置 Skill，开箱即用。

| Skill | 功能 |
|-------|------|
| `code-review` | 代码审查（安全、风格、逻辑） |
| `debug` | 系统化调试（错误分析、复现、修复） |
| `refactor` | 重构任务（拆分大函数、提取接口、优化结构） |
| `git-commit` | 生成规范化 commit message（Conventional Commits） |
| `security-audit` | 安全审计（OWASP Top 10、依赖漏洞） |
| `doc-gen` | 生成文档（README、API 文档、架构图） |

---

### Phase 4：Hooks 机制 — 预计 3-4 天

**目标**：支持生命周期 Hooks，用户可以在 `.vicorc.json` 中配置 shell 命令在特定节点执行。

#### 支持的 Hook 事件（精选 6 种最核心）

| 事件 | 触发时机 | 典型用途 |
|------|---------|---------|
| `SessionStart` | 会话开始时 | 加载额外上下文、初始化环境 |
| `PreToolUse` | 工具调用前 | 拦截危险操作、参数校验 |
| `PostToolUse` | 工具调用成功后 | 自动格式化、运行 lint |
| `PostToolUseFailure` | 工具调用失败后 | 错误通知、日志记录 |
| `Stop` | Agent 响应结束时 | 汇总报告、发送通知 |
| `StopFailure` | 因错误结束时 | 告警通知 |

#### 配置格式（`.vicorc.json`）

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "write|edit",
        "command": "bash",
        "args": ["-c", "ruff format $VICO_TOOL_INPUT_PATH 2>/dev/null || true"],
        "timeout_ms": 5000
      }
    ],
    "SessionStart": [
      {
        "command": "bash",
        "args": ["-c", "cat .vico/context.md 2>/dev/null || true"]
      }
    ]
  }
}
```

#### Hook 执行结果处理

| 退出码 | 含义 | Agent 行为 |
|--------|------|-----------|
| `0` | 成功 | 解析 stdout JSON（若有），注入上下文 |
| `2` | 阻断 | 拒绝工具调用，将 stderr 作为错误原因返回给模型 |
| 其他 | 警告 | 打印 stderr，继续执行原始操作 |

---

### Phase 5：`/skill` 命令增强（可选） — 1 天

- `/skills reload` — 重新扫描 Skill 目录
- `/skills info <name>` — 显示 Skill 详情（frontmatter + 文件列表）
- Tab 补全支持（prompt_toolkit completer）

---

## 六、配置参考与示例

### 6.1 `.vicorc.json` 新增配置项

```json
{
  "skills": {
    "enabled": true,               // Skills 功能总开关（默认 true）
    "scan_paths": [],              // 额外扫描路径（除默认的 .vico/skills/ 和 ~/.vico/skills/）
    "auto_activate": true,         // 允许模型自动激活 Skill（默认 true）
    "show_activation_panel": true  // 激活时展示面板（默认 true）
  },
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "write|edit",
        "command": "ruff",
        "args": ["format", "--silent", "."],
        "timeout_ms": 3000
      }
    ]
  }
}
```

### 6.2 完整的 Skill 示例：code-review

```
.vico/skills/code-review/
├── SKILL.md
└── checklist.md
```

**SKILL.md**:
```markdown
---
name: Code Review
description: |
  Perform a thorough code review focusing on correctness, security, performance,
  and code style. Use this skill when the user asks to review, audit, or check code.
argument-hint: "[file-or-directory]"
---

# Code Review Instructions

You are performing a professional code review. Follow this checklist systematically.

## Current Repository State
!`git diff HEAD --stat`

## Checklist

@./checklist.md

## Output Format

For each finding, use:
```
[SEVERITY] file.py:line — Issue description
  → Suggestion: what to do instead
```

Severity levels: CRITICAL / HIGH / MEDIUM / LOW / INFO

## Arguments
Target: $ARGUMENTS (if empty, review all recent changes)
```

**checklist.md**:
```markdown
### Security
- [ ] No hardcoded credentials or API keys
- [ ] No SQL injection vulnerabilities
- [ ] No path traversal risks

### Correctness  
- [ ] Edge cases handled (None, empty, overflow)
- [ ] Error handling is complete
- [ ] No off-by-one errors

### Performance
- [ ] No N+1 queries
- [ ] No unnecessary re-computation in loops
```

### 6.3 带 `disable-model-invocation` 的部署 Skill

```markdown
---
name: Deploy to Staging
description: Deploy the current branch to the staging environment
command: true
disable-model-invocation: true   # 只允许用户手动 /skill deploy
risk-level: high
allowed-tools: ["bash"]
---

# Deploy Instructions

**⚠️ This will deploy to staging. Confirm before proceeding.**

Branch: !`git branch --show-current`
Last commit: !`git log --oneline -1`

## Steps
1. Run tests: `bash -c "uv run pytest -q"`
2. Build: `bash -c "docker build -t myapp:staging ."`
3. Push: `bash -c "docker push registry.example.com/myapp:staging"`
4. Deploy: `bash -c "kubectl set image deployment/myapp app=registry.example.com/myapp:staging"`

## Arguments
Environment override: $ARGUMENTS (default: staging)
```

---

## 七、设计决策记录（ADR）

### ADR-001：为什么采用 agentskills.io 标准，而不是自定义格式？

**决策**：Vico Skill 采用与 Claude Code、OpenAI、Gemini CLI 相同的 SKILL.md 格式（agentskills.io 标准）。

**理由**：
1. **生态互操作性**：用户可以直接复用为其他 Agent 编写的 Skill，无需转换
2. **认知成本最低**：开发者只需学一种格式
3. **未来兼容性**：标准持续演进，跟随标准可免费获得新特性

---

### ADR-002：为什么 Skill 内容在激活后注入 user 消息而非 system 消息？

**决策**：`activate_skill` 将 Skill 正文以带标签的 user 消息形式注入：
```
<skill_activated name="code-review">
...skill body...
</skill_activated>
```

**理由**：
1. **system prompt 不支持动态变化**：Vico 的 system prompt 在会话开始时一次性构建（`@lru_cache`），激活 Skill 后若修改 system prompt 需重建，成本高
2. **与 Claude Code 一致**：Claude Code 的 Skill 正文也以对话历史形式注入
3. **符合上下文语义**：Skill 是"用户在这一轮给 Agent 追加的专项指令"，语义上更接近 user 消息
4. **上下文压缩友好**：Skill 内容与其他消息一样参与压缩，不会形成不可压缩的特殊区块

---

### ADR-003：为什么 Skill 摘要注入 system prompt，而 Skill 正文在激活时注入？

**决策**：采用分阶段注入策略（Gemini CLI 的激活确认模式 + Claude Code 的自动发现模式的结合）。

**理由**：
1. **上下文窗口保护**：一个项目可能有 10+ 个 Skill，如果每个都预先注入完整正文，会消耗大量 token
2. **模型仍能发现 Skill**：摘要（name + description）足以让模型判断是否需要激活某 Skill
3. **保持懒加载原则**：与 Vico 对工具定义的处理一致——工具定义始终传递，但工具执行只在需要时发生

---

### ADR-004：为什么 Hooks 仅支持 6 种事件而非 Claude Code 的 27 种？

**决策**：Phase 4 只实现 `SessionStart`、`PreToolUse`、`PostToolUse`、`PostToolUseFailure`、`Stop`、`StopFailure` 共 6 种。

**理由**：
1. **覆盖最高频需求**：调研显示 95% 的 Hook 使用场景集中在这 6 类事件
2. **降低实现复杂度**：27 种事件（SubagentStart/Stop、WorktreeCreate/Remove 等）需要整套子 Agent 架构支撑
3. **MVP 优先**：先实现最有价值的 Hook，后续按需扩展
4. **避免过度设计**：Vico 目前是单 Agent 模型，Subagent 相关事件暂无意义

---

### ADR-005：如何处理 Skill 与现有 `/xxx` 命令系统的冲突？

**决策**：
- `/skills` — 专属的 Skills 管理命令（list、reload、info）
- `/skill <name>` — 手动激活 Skill（不同于 `/skills`，注意单复数区分）
- 用户在 SKILL.md 中设 `command: true` 且 `name: deploy` → 自动注册 `/deploy` 命令（覆盖同名内置命令，并打印警告）

**冲突解决顺序**：内置命令（/clear、/model、/help、/exit）> Skill 注册命令 > 自然语言传入 Agent

---

## 附录：快速上手

### 创建你的第一个 Skill

1. **创建目录**：
   ```bash
   mkdir -p .vico/skills/my-skill
   ```

2. **创建 SKILL.md**：
   ```bash
   cat > .vico/skills/my-skill/SKILL.md << 'EOF'
   ---
   name: My First Skill
   description: |
     A template skill that demonstrates the SKILL.md format.
     Activate this when testing the skill system.
   argument-hint: "[optional-argument]"
   ---
   
   # My First Skill
   
   Current directory: ${VICO_CWD}
   Skill directory: ${VICO_SKILL_DIR}
   Your argument: $ARGUMENTS
   
   ## What I Do
   
   I am a demonstration skill. Here you would write your specialized instructions.
   EOF
   ```

3. **验证 Skill 被发现**：
   ```bash
   vico
   > /skills
   ```
   
   输出：
   ```
   Available Skills
   /skill my-skill [optional-argument]  A template skill that demonstrates...
   ```

4. **激活 Skill**：
   ```bash
   > /skill my-skill hello-world
   ```
   
   或通过自然语言：
   ```bash
   > 用 my-skill 帮我试试
   ```
