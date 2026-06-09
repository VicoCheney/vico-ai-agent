# Vico AI Agent 🤖

> An all-powerful AI agent assistant, armed with imagination.

## 功能特性

- 🔄 **Agent Loop** — Think → Act → Observe 循环，支持多轮工具调用
- 📋 **Planning Phase** — 复杂任务前置规划，自动识别并批量调度独立工具调用（通过提示词引导，无独立 Planner LLM 调用）
- 🔧 **5 个核心工具**：
  - `read` — 读取文件内容，支持行范围
  - `write` — 创建或覆盖文件（整文件写入，中风险）
  - `edit` — 精确字符串替换编辑文件（中风险）
  - `bash` — 执行 Shell 命令（高风险，需确认）
  - `search` — 正则搜索代码（优先使用 ripgrep）
- 🛡️ **权限控制** — 高风险操作前弹出确认框，支持"本次批准 / 本会话始终批准 / 拒绝"
- 💭 **Thinking 展示** — 实时展示模型推理过程（动态 spinner + 摘要片段）
- 🎨 **终端 UI** — 彩色 Spinner、比例对齐的工具执行行、Markdown 渲染
- 🔀 **多 Provider** — 内置 MiMo (Xiaomi) 和 DeepSeek，运行时 `/model` 热切换

---

## 快速开始

> 已有一键安装脚本，clone 后一条命令即可完成所有配置并启动。

### 方式一：一键安装（推荐）

```bash
git clone https://github.com/VicoCheney/vico-ai-agent.git
cd vico-ai-agent
bash setup.sh
```

**可选参数：**

```bash
bash setup.sh --global     # 同时全局安装，任意目录可直接运行 vico
bash setup.sh --no-launch  # 完成安装后不自动启动
bash setup.sh --help       # 查看所有参数说明
```

---

### 方式二：手动安装（分步）

> 以下步骤适用于**全新 macOS**，从零开始到跑起来。

### 第 0 步：安装系统前置依赖

```bash
# 1. 安装 Homebrew（如果已有则跳过）
/bin/bash -c "$(curl -fsSF https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# 2. 安装 Python 3.11+（uv 会自动管理，但需要系统有 curl/git）
#    macOS 自带的 Python 通常已满足要求，也可以用 Homebrew 安装
brew install python@3.12   # 可选，uv 会自动下载所需版本

# 3. 安装 uv（Python 包管理器，必须）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 安装完成后重新加载 shell，让 uv 命令生效
source ~/.zshrc   # 或 source ~/.bashrc
```

> **验证安装**：运行 `uv --version`，看到版本号即成功。

---

### 第 1 步：克隆项目

```bash
git clone https://github.com/VicoCheney/vico-ai-agent.git
cd vico-ai-agent
```

---

### 第 2 步：安装 Python 依赖

```bash
uv sync
```

uv 会自动创建虚拟环境并安装所有依赖（首次约需 30 秒）。

---

### 第 3 步：配置 API Key

```bash
cp .env.example .env
```

用任意文本编辑器打开 `.env`，填入你的 API Key：

```dotenv
# 默认使用 MiMo（小米）作为 Provider，填入 MiMo Token Plan Key：
MIMO_API_KEY=tp-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# 如果想用 DeepSeek，填入 DeepSeek API Key：
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

**获取 API Key**：
- **MiMo**（默认）：[platform.xiaomimimo.com](https://platform.xiaomimimo.com) → 控制台 → Token Plan → 创建 API Key（格式 `tp-xxxxx`）
- **DeepSeek**：[platform.deepseek.com](https://platform.deepseek.com) → API Keys → 创建（格式 `sk-xxxxx`）

> 只需填入你实际使用的那个 Provider 的 Key 即可。

---

### 第 4 步：（可选）调整项目配置

项目根目录的 `.vicorc.json` 控制默认 Provider / 模型和各项参数，默认已配置好，直接跳过也可以。

如需切换默认 Provider 为 DeepSeek：

```json
"llm": {
  "default": {
    "provider": "deepseek",
    "model": "deepseek-v4-flash"
  }
}
```

---

### 第 5 步：运行！

```bash
uv run vico
```

看到 ASCII Logo 和提示符即表示启动成功，可以开始对话了。

```
  ██╗   ██╗ ██╗  ██████╗  ██████╗
  ██║   ██║ ██║ ██╔════╝ ██╔═══██╗
  ██║   ██║ ██║ ██║      ██║   ██║
  ╚██╗ ██╔╝ ██║ ██║      ██║   ██║
   ╚████╔╝  ██║ ╚██████╗ ╚██████╔╝
    ╚═══╝   ╚═╝  ╚═════╝  ╚═════╝

  All-powerful AI agent assistant · Armed with imagination

👤 You: 帮我检查一下这个项目有没有 TODO 没完成的
```

---

### 第 6 步：（可选）全局安装

完成前 5 步后，`vico` 只能在项目目录内用 `uv run vico` 启动。若想在**任意目录**下直接使用 `vico` 命令，执行全局安装：

```bash
uv tool install . --force --editable
# 或使用安装脚本：bash setup.sh --global
```

之后在终端任意位置输入 `vico` 即可启动：

```bash
cd ~/Desktop
vico   # 👈 直接在桌面上启动 Vico
```

卸载：

```bash
uv tool uninstall vico-ai-agent
```

## 执行策略

Vico 采用 **Planning + 批量执行** 模型以减少 LLM 调用次数。

### Planning Protocol

当用户的请求涉及 3 个以上工具调用，或明显是多步任务时，模型会在执行任何工具之前先在内部产出结构化的 `<plan>` 块（内部规划，不展示在终端）。

`<plan>` 块中每个步骤标注 `[batch]`（可并行）或 `[seq]`（顺序依赖），模型据此在同一 LLM 轮次内批量发出多个工具调用：

```
Steps:
  1. [batch] bash: sw_vers + bash: sysctl ... + bash: df -h + bash: vm_stat
  2. [batch] bash: netstat ... + bash: ifconfig ...
  3. [seq]   read: config.py  →  edit: config.py
```

### 批量并发执行

AgentLoop 支持在单个 LLM 轮次内并发执行多个工具调用（`asyncio.gather`），将原来 O(N) 的 LLM round-trips 降低到接近 O(log N)。

---



## 交互命令

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助和可用命令 |
| `/model` | 查看当前 Provider / 模型 |
| `/model <provider/model>` | 切换 Provider / 模型（运行时热切换）示例：`/model deepseek/deepseek-v4-flash` |
| `/clear` | 清空对话历史，开启新会话 |
| `/exit` 或 `Ctrl+D` | 退出 |
| `Ctrl+C` | 中断当前正在执行的任务（再按一次退出） |

---

## 权限级别

Vico 在执行工具前会根据风险等级决定是否需要用户确认（由 `.vicorc.json` 的 `tools.auto_approve` 控制）：

| 级别 | 工具 | 默认行为 |
|------|------|------|
| `low` | `read`, `search` | 自动执行，无需确认 |
| `medium` | `write`, `edit` | 由 `auto_approve` 配置决定 |
| `high` | `bash` | 弹出权限确认框 |

> 默认配置 `auto_approve: ["low", "medium"]`，即 `write` / `edit` 也自动执行。如需更严格的保护，可改为 `["low"]`。

权限确认框操作（仅 `high` 风险或未自动批准的工具会显示）：
- **方向键** 左右选择 `Once` / `Always` / `Deny`
- **Enter** 确认选择
- **`Ctrl+C`** 拒绝并中止当前任务

---

## 项目结构

```
src/vico/
├── core/
│   ├── types.py                  # 核心类型定义
│   ├── agent_loop.py             # Agent 主循环：Think → Act → Observe
│   ├── context_manager.py        # 上下文窗口管理 + Token 压缩
│   ├── permission_controller.py  # 工具执行权限控制
│   ├── prompt_loader.py          # Jinja2 系统提示词加载器
│   └── system_prompt.py          # 系统提示词构建（变量注入）
├── llm/
│   ├── llm_factory.py            # Provider 工厂（读取 .vicorc.json）
│   ├── models.py                 # 支持的模型注册表
│   └── providers/
│       ├── base.py               # OpenAI-compatible 共享基类
│       ├── deepseek.py           # DeepSeek Provider
│       └── mimo.py               # MiMo (Xiaomi) Provider
├── tools/
│   ├── __init__.py               # 内置工具列表
│   ├── registry.py               # 工具注册表 + 调度器
│   ├── read.py                   # read 工具
│   ├── write.py                  # write 工具（创建/覆盖整文件）
│   ├── bash.py                   # bash 工具
│   ├── edit.py                   # edit 工具
│   └── search.py                 # search 工具（ripgrep / grep）
├── cli/
│   ├── __init__.py               # CLI 入口 + REPL 主循环
│   └── renderer.py               # 终端 UI 渲染器
└── config.py                     # 配置加载（.env + .vicorc.json）
```

---

## 配置参考

### `.env` — API Keys

| 变量 | 说明 |
|------|------|
| `MIMO_API_KEY` | MiMo Token Plan API Key（`tp-xxxxx`） |
| `DEEPSEEK_API_KEY` | DeepSeek API Key（`sk-xxxxx`） |

### `.vicorc.json` — 项目级配置

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
    "max_tokens": 1000000,         // 上下文窗口上限
    "reserve_tokens": 131072,      // 为输出保留的 token 数
    "compression_threshold": 0.85  // 触发压缩的使用率阈值
  },
  "tools": {
    "auto_approve": ["low", "medium"],  // 自动批准的风险级别（low=read/search，medium=write/edit）
    "timeout_ms": 30000                 // 工具执行超时（毫秒）
  }
}
```

---

## 开发

```bash
# 运行
uv run vico

# Lint
uv run ruff check src/
uv run ruff format src/

# 类型检查
uv run mypy src/vico
```
