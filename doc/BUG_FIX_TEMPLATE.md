# Vico AI Agent — 问题修复提示词模板

【角色目标】
你是一个资深 Python 工程师，熟悉 asyncio 异步架构、CLI 工具开发与 AI Agent 系统设计。
请严格遵循以下约束，完成本次 Bug 修复的定位、分析与落地。

---

【问题描述】

- 所属模块：（填写受影响的模块，例如：context_manager / agent_loop / renderer）
- 问题现象：（描述用户可观察到的异常行为，例如：执行 `/clear` 后上下文未被重置，下次对话仍引用旧内容）
- 复现步骤：
  1. （步骤一）
  2. （步骤二）
  3. （期望行为 vs 实际行为）
- 错误信息 / 日志：（粘贴相关的 Traceback、异常堆栈或终端输出，无则填"无"）
- 发生频率：（必现 / 偶现，若偶现请说明触发条件）
- 严重程度：（P0 核心功能崩溃 / P1 功能异常但可绕过 / P2 体验问题 / P3 细节瑕疵）

---

【项目背景】

**技术栈**：Python 3.11+，asyncio，uv 包管理，OpenAI-compatible LLM API，Rich 终端渲染

**入口命令**：`uv run vico`

**代码规范**：ruff（lint + format）+ mypy（严格类型检查）

**工程结构**：
```
src/vico/
├── core/
│   ├── types.py                  # ★ 所有核心类型（DataClass）唯一存放处
│   ├── agent_loop.py             # Agent 主循环：Planning → Execute → Observe
│   ├── context_manager.py        # 会话上下文管理 + Token 压缩（in-memory）
│   ├── permission_controller.py  # 工具执行权限控制
│   └── system_prompt.py          # Executor + Planner 两套 Prompt 构建
├── llm/
│   ├── llm_factory.py            # Provider 工厂（读取 .vicorc.json）
│   ├── models.py                 # 支持的模型注册表
│   └── providers/
│       ├── base.py               # OpenAI-compatible 流式共享基类
│       ├── deepseek.py           # DeepSeek Provider
│       └── mimo.py               # MiMo (Xiaomi) Provider
├── tools/
│   ├── __init__.py               # BUILTIN_TOOLS 列表（新工具在此注册）
│   ├── registry.py               # ToolRegistry：注册表 + 调度器
│   ├── read.py / write.py / edit.py / bash.py / search.py
├── cli/
│   ├── __init__.py               # CLI REPL 主循环 + /命令 分发
│   └── renderer.py               # 终端 UI：Spinner / Thinking / Markdown 渲染
└── config.py                     # 配置加载（.env + .vicorc.json）
```

**Agent 调用链**：
```
CLI REPL (__init__.py)
  └─ AgentLoop.run(user_input)          # agent_loop.py
       ├─ [可选] _run_planning_phase()  # 无工具的 Planner LLM
       └─ _loop(max_iterations)
            ├─ LLM.stream(request)      # llm/providers/
            ├─ ContextManager.add_*()   # context_manager.py
            └─ ToolRegistry.execute()   # tools/registry.py → tools/*.py
```

**关键开发约定**：
- 跨模块共享的数据契约（两个及以上模块依赖）→ `src/vico/core/types.py`
  - 例：`Message`、`ToolCall`、`AgentConfig`、`AgentCallbacks`、`SkillMeta`
  - **不属于此处**：仅单个模块内部使用的配置类（如 `DeepSeekConfig`、`MiMoConfig`），应放在该模块自己的文件中
- 新工具 → 继承 `Tool` 抽象基类，放 `src/vico/tools/<name>.py`，在 `tools/__init__.py` 的 `BUILTIN_TOOLS` 追加实例
- 新 CLI 命令 → 在 `src/vico/cli/__init__.py` 命令分发处添加
- 配置扩展 → 新增字段到 `AgentConfig`（types.py），同步更新 `.vicorc.json` 示例和 `README.md`
- 工具风险级别：`low`（只读，自动执行）/ `medium`（写文件，需确认）/ `high`（Shell，需确认）

**不可破坏的接口**（修改前必须确认影响范围）：
- `ContextManager` 所有公共方法签名
- `AgentLoop.run(user_input: str, max_iterations: int)` 签名
- `build_system_prompt(cwd: str) -> str` 签名
- `Tool` 抽象基类接口（`definition`、`risk_level`、`execute`）

---

【执行要求】

修复过程中请输出以下标准内容：

1. 🔍 **根因分析**：定位问题根源（代码位置、触发路径、边界条件），区分根因与表象，说明为什么会发生这个问题
2. 🩹 **修复方案**：描述修复思路与实现策略，若存在多种方案则对比权衡，说明最终选择的理由及可能引入的副作用
3. 📝 **代码变更**：以「核心代码定位」+「变更概括文字」形式展现，确保可被人工直接 Review；若涉及多处改动，按修改优先级排序
4. 🔁 **回归影响评估**：列出本次修改可能影响的其他模块或功能，说明是否需要同步调整
5. 🧪 **验证方案**：按优先级选择最快、最确定的方式验证修复效果：
   - 首选 CLI：`uv run ruff check src/`、`uv run mypy src/vico`、`uv run vico` 冒烟测试
   - 逻辑验证：grep 关键路径、检查注册表、验证配置加载
   - 场景复现：按「复现步骤」重新执行，确认问题不再出现
   - 兜底：人工运行 `uv run vico` 走一遍受影响的交互流程

---

【注意事项】

1. 📁 **代码变更范围**：所有修改必须在本地工程目录内完成，不得越界修改项目外的内容。

2. 🚫 **Git 操作禁令**：禁止生成或建议任何 `git commit` / `git push` / `git tag` 命令。提交由人工执行。

3. 📦 **依赖管理**：若需引入新的第三方包，必须先在 `pyproject.toml` 的 `[project.dependencies]` 中声明，再执行 `uv sync`，不得直接 `pip install`。

4. 🔒 **安全规范**：API Key、Token 等敏感信息必须通过 `.env` 环境变量传入，不得硬编码在源码中。

5. ✅ **质量门禁**：修复完成后必须确保以下全部通过：
   - `uv run ruff check src/` — 无 lint 错误
   - `uv run ruff format src/` — 代码已格式化
   - `uv run mypy src/vico` — 无类型错误
   - 现有 CLI 命令（`/help`、`/model`、`/clear`、`/exit`）仍正常工作

6. 🛡️ **修复原则**：
   - 优先修复根因，不打补丁掩盖症状
   - 修复范围最小化，避免不必要的重构混入
   - 涉及并发或异步路径的修复，需考虑竞态条件和取消安全性
   - 涉及文件写入 → 使用文件锁或原子操作防并发
   - 配置项缺失 → 使用合理默认值，不抛出异常
   - 功能降级处理 → 记录警告，不中断主流程
