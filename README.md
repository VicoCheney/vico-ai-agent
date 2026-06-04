# Vico AI Agent 🤖

> A minimal AI coding assistant, inspired by Claude Code and Codex. Built in Python, powered by MiMo / DeepSeek.

## 功能特性

- 🔄 **Agent Loop** — Think → Act → Observe 循环，支持多轮工具调用
- 🔧 **3 个核心工具**：
  - `read_file` — 读取文件内容，支持行范围
  - `execute_command` — 执行 Shell 命令（高风险，需确认）
  - `search` — 正则搜索代码（优先使用 ripgrep）
- 🛡️ **权限控制** — 危险操作前弹出确认框，支持"本次批准 / 本会话始终批准 / 拒绝"
- 💭 **Thinking 展示** — 实时流式展示模型推理过程
- 🎨 **终端 UI** — 彩色 Spinner、比例对齐的工具执行行、Markdown 渲染
- 🔀 **多 Provider** — 内置 MiMo (Xiaomi) 和 DeepSeek，运行时 `/model` 热切换

---

## 快速开始

> 以下步骤适用于**全新 macOS**，从零开始到跑起来。

### 第 0 步：安装系统前置依赖

```bash
# 1. 安装 Homebrew（如果已有则跳过）
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

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

看到 ASCII Logo 和提示符 `❯` 即表示启动成功，可以开始对话了。

```
  ██╗   ██╗ ██╗  ██████╗  ██████╗
  ██║   ██║ ██║ ██╔════╝ ██╔═══██╗
  ██║   ██║ ██║ ██║      ██║   ██║
  ╚██╗ ██╔╝ ██║ ██║      ██║   ██║
   ╚████╔╝  ██║ ╚██████╗ ╚██████╔╝
    ╚═══╝   ╚═╝  ╚═════╝  ╚═════╝

  All-powerful AI agent assistant · Armed with imagination

❯ 帮我检查一下这个项目有没有 TODO 没完成的
```

---

### 第 6 步：（可选）全局安装

完成前 5 步后，`vico` 只能在项目目录内用 `uv run vico` 启动。若想在**任意目录**下直接使用 `vico` 命令，执行全局安装：

```bash
uv tool install . --force --editable
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

---

## 交互命令

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助和可用命令 |
| `/model` | 切换 Provider / 模型（运行时热切换） |
| `/clear` | 清空对话历史，开启新会话 |
| `/exit` 或 `Ctrl+D` | 退出 |
| `Ctrl+C` | 中断当前正在执行的任务（再按一次退出） |

---

## 权限级别

Vico 在执行工具前会根据风险等级决定是否需要用户确认：

| 级别 | 工具 | 行为 |
|------|------|------|
| `low` | `read_file`, `search` | 自动执行，无需确认 |
| `high` | `execute_command` | 弹出权限确认框 |

权限确认框选项：
- **`y`** 或直接回车 → 本次批准
- **`a`** → 本会话始终批准（该工具不再询问）
- **`n`** → 拒绝执行

---

## 项目结构

```
src/vico/
├── core/
│   ├── types.py                  # 核心类型定义
│   ├── agent_loop.py             # Agent 主循环 (Think → Act → Observe)
│   ├── context_manager.py        # 上下文窗口管理 + Token 压缩
│   ├── permission_controller.py  # 工具执行权限控制
│   └── system_prompt.py          # 系统 Prompt 构建（含 Git 信息）
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
│   ├── read_file.py              # read_file 工具
│   ├── execute_command.py        # execute_command 工具
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
    "auto_approve": ["low"],       // 自动批准的风险级别
    "timeout_ms": 30000            // 工具执行超时（毫秒）
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
