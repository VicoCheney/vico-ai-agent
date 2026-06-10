# Vico AI Agent — 新需求变更提示词模板

【角色目标】
你是一个资深 Python 工程师，熟悉 asyncio 异步架构、CLI 工具开发与 AI Agent 系统设计。
请严格遵循以下约束，完成本次需求的开发与落地。

---

【需求描述】

- 所属模块：系统提示词
- 需求背景：当前的agent系统提示词全部都凝结在system_prompt.py 文件里，不利于拆分和维护，且后续会越滚越大，必需治理
- 具体功能要求：
  - 将 system_prompt.py 中的build_system_prompt硬编码的 prompt 拆分出来，形成独立的 prompt 模板文件
  - 提供一套 prompt 模板加载机制，支持热加载和缓存
  - 保证 build_system_prompt 函数的调用逻辑和返回结果不变
  - 将拆分出来的提示词分层归类化，类似于一本书的目录一样，顶层有一个Agent.md，这个就是agent的目录，它里面就是一个目录内容，展示agent每个部分的信息，例如soul、goal、tools、api等等的内容，最细粒度拆分，后面跟着一个该内容的md文件具体引用或者路径，便于组建agent系统提示词时快速找到内容并拼接
  - 提示词的拆分必需最细粒度，按照功能模块拆，确保职责唯一、内容不耦合
  - 提示词的拆分必须区分渐进式加载的内容和常加载的内容，把这个目录按照渐进式加载的内容和常加载区分出来，常加载的内容必须在组建系统提示词时把原文填入，渐进式加载的内容仅透出文件所在的引用或者路径，便于agent在有需要时自主取用，以节约系统提示词长度。
  - 所有拆分后的md都放在doc/下，拆出来一个渐进式和常加载的目录来放置
- 其他说明：略

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

开发过程中请输出以下标准内容：

1. 📐 **方案设计**：功能拆解、受影响模块、关键交互流程（可用伪代码或流程图辅助说明）
2. 💡 **技术选型**：关键实现思路、备选方案对比、最终选择理由及潜在风险
3. 📝 **代码变更**：以「核心代码定位」+「变更概括文字」形式展现，确保可被人工直接 Review
4. 🧪 **验证方案**：按优先级选择最快、最确定的方式验证：
   - 首选 CLI：`uv run ruff check src/`、`uv run mypy src/vico`、`uv run vico` 冒烟测试
   - 逻辑验证：grep 关键路径、检查注册表、验证配置加载
   - 兜底：人工运行 `uv run vico` 走一遍受影响的交互流程

---

【注意事项】

1. 📁 **代码变更范围**：所有修改必须在本地工程目录内完成，不得越界修改项目外的内容。

2. 🚫 **Git 操作禁令**：禁止生成或建议任何 `git commit` / `git push` / `git tag` 命令。提交由人工执行。

3. 📦 **依赖管理**：若需引入新的第三方包，必须先在 `pyproject.toml` 的 `[project.dependencies]` 中声明，再执行 `uv sync`，不得直接 `pip install`。

4. 🔒 **安全规范**：API Key、Token 等敏感信息必须通过 `.env` 环境变量传入，不得硬编码在源码中。

5. ✅ **质量门禁**：实现完成后必须确保以下全部通过：
   - `uv run ruff check src/` — 无 lint 错误
   - `uv run ruff format src/` — 代码已格式化
   - `uv run mypy src/vico` — 无类型错误
   - 现有 CLI 命令（`/help`、`/model`、`/clear`、`/exit`）仍正常工作

6. 🛡️ **边界条件默认原则**：
   - 配置项缺失 → 使用合理默认值，不抛出异常
   - 功能开关关闭 → 返回友好提示，不报错
   - 文件 I/O 失败 → 记录警告，降级处理，不中断主流程
   - 涉及文件写入 → 使用文件锁或原子操作防并发
