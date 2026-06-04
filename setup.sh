#!/usr/bin/env bash
# =============================================================================
#  Vico AI Agent — 一键安装 & 启动脚本（幂等版）
#  用法：bash setup.sh [--global] [--no-launch]
#    --global     同时执行全局安装（任意目录可直接运行 vico）
#    --no-launch  完成安装后不自动启动
# =============================================================================
#
#  幂等保证：
#    • Homebrew ── 已安装则跳过，不执行 brew install 或 brew update
#    • uv        ── 已安装则跳过，不重新下载
#    • uv sync   ── .venv 存在且与 uv.lock 一致则跳过（--frozen 检查）
#    • .env      ── 已存在则跳过复制；已有有效 key 则不再提示输入
#    • 全局安装  ── 已安装且版本一致则跳过（--global 参数显式触发除外不重装）
#
# =============================================================================

set -euo pipefail

# ─── 颜色 ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
RESET='\033[0m'

ok()   { echo -e "${GREEN}✓${RESET}  $*"; }
info() { echo -e "${CYAN}→${RESET}  $*"; }
warn() { echo -e "${YELLOW}⚠${RESET}  $*"; }
fail() { echo -e "${RED}✗${RESET}  $*" >&2; exit 1; }
step() { echo -e "\n${BOLD}${CYAN}[ $* ]${RESET}"; }
dim()  { echo -e "${DIM}   $*${RESET}"; }
skip() { echo -e "${DIM}⟳  $* — 已是最新，跳过${RESET}"; }

# ─── 参数解析 ─────────────────────────────────────────────────────────────
GLOBAL_INSTALL=false
AUTO_LAUNCH=true
for arg in "$@"; do
  case "$arg" in
    --global)    GLOBAL_INSTALL=true ;;
    --no-launch) AUTO_LAUNCH=false ;;
    -h|--help)
      echo "用法: bash setup.sh [--global] [--no-launch]"
      echo "  --global      安装完成后执行全局安装（任意目录可用 vico 命令）"
      echo "  --no-launch   安装完成后不自动启动 vico"
      exit 0
      ;;
    *) warn "未知参数: $arg，忽略" ;;
  esac
done

# ─── 确保在项目根目录 ─────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
[[ -f "pyproject.toml" ]] || fail "请在 vico-ai-agent 项目根目录下运行此脚本。"

echo ""
echo -e "${BOLD}${CYAN}  ██╗   ██╗ ██╗  ██████╗  ██████╗  ${RESET}"
echo -e "${BOLD}${CYAN}  ██║   ██║ ██║ ██╔════╝ ██╔═══██╗ ${RESET}"
echo -e "${BOLD}${CYAN}  ██║   ██║ ██║ ██║      ██║   ██║ ${RESET}"
echo -e "${BOLD}${CYAN}  ╚██╗ ██╔╝ ██║ ██║      ██║   ██║ ${RESET}"
echo -e "${BOLD}${CYAN}   ╚████╔╝  ██║ ╚██████╗ ╚██████╔╝ ${RESET}"
echo -e "${BOLD}${CYAN}    ╚═══╝   ╚═╝  ╚═════╝  ╚═════╝  ${RESET}"
echo ""
echo -e "${DIM}  Vico AI Agent — 一键安装脚本${RESET}"
echo ""

# =============================================================================
# Step 0: 检查系统环境
# =============================================================================
step "Step 0 / 检查系统环境"

OS="$(uname -s)"
if [[ "$OS" != "Darwin" ]]; then
  warn "当前系统为 $OS，本脚本主要针对 macOS 优化。Linux 也应可用，但未经完整测试。"
fi

# 检查 curl（Homebrew / uv 安装都需要）
command -v curl &>/dev/null || fail "未找到 curl，请先安装 curl 后重试。"
ok "curl 已就绪"

# =============================================================================
# Step 1: 检测 / 安装 Homebrew（macOS 专属）
# =============================================================================
step "Step 1 / Homebrew"

if [[ "$OS" == "Darwin" ]]; then
  if command -v brew &>/dev/null; then
    skip "Homebrew $(brew --version | head -1)"
  else
    info "未检测到 Homebrew，开始安装..."
    dim "这可能需要几分钟，请耐心等待..."
    NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" \
      || fail "Homebrew 安装失败，请手动安装后重试：https://brew.sh"
    # 安装后更新当前 shell 的 PATH（Apple Silicon / Intel 路径不同）
    if [[ -f "/opt/homebrew/bin/brew" ]]; then
      eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [[ -f "/usr/local/bin/brew" ]]; then
      eval "$(/usr/local/bin/brew shellenv)"
    fi
    ok "Homebrew 安装完成"
  fi
else
  dim "非 macOS，跳过 Homebrew 安装"
fi

# =============================================================================
# Step 2: 检测 / 安装 uv
# =============================================================================
step "Step 2 / uv（Python 包管理器）"

# 将常见安装路径加入 PATH，确保新安装的 uv 即时可用
export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"

if command -v uv &>/dev/null; then
  skip "uv $(uv --version)"
else
  info "未检测到 uv，开始安装..."
  dim "安装到 ~/.local/bin/uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh \
    || fail "uv 安装失败，请访问 https://docs.astral.sh/uv/ 手动安装。"
  export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"
  command -v uv &>/dev/null || fail "uv 安装后仍无法找到，请重开终端后重试。"
  ok "uv 安装完成：$(uv --version)"

  # 写入 shell profile，让后续 session 也能找到 uv（只写一次）
  SHELL_RC=""
  if   [[ -f "$HOME/.zshrc" ]];   then SHELL_RC="$HOME/.zshrc"
  elif [[ -f "$HOME/.bashrc" ]];  then SHELL_RC="$HOME/.bashrc"
  elif [[ -f "$HOME/.profile" ]]; then SHELL_RC="$HOME/.profile"
  fi
  if [[ -n "$SHELL_RC" ]] && ! grep -qF '$HOME/.local/bin' "$SHELL_RC" 2>/dev/null; then
    printf '\n# uv — Python package manager\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$SHELL_RC"
    dim "已将 uv 路径写入 $SHELL_RC（重开终端后永久生效）"
  fi
fi

# =============================================================================
# Step 3: 安装 Python 依赖（幂等：锁文件一致时跳过）
# =============================================================================
step "Step 3 / Python 依赖"

# uv sync --frozen：仅验证 lock 文件一致性，不修改任何内容；
# 若 .venv 不存在或依赖与 uv.lock 不符则退出码非0，此时才执行完整 sync。
# 这保证了"已安装且一致"时零网络请求、零文件写入。
NEEDS_SYNC=false
if [[ ! -d ".venv" ]]; then
  NEEDS_SYNC=true
  dim ".venv 不存在，需要初始化"
elif ! uv sync --frozen --quiet 2>/dev/null; then
  NEEDS_SYNC=true
  dim ".venv 与 uv.lock 不一致，需要重新同步"
fi

if $NEEDS_SYNC; then
  info "正在同步虚拟环境..."
  dim "首次运行约需 30 秒，后续几乎即时"
  uv sync || fail "uv sync 失败，请检查网络连接后重试。"
  ok "依赖安装完成"
else
  skip "虚拟环境（.venv 与 uv.lock 一致）"
fi

# =============================================================================
# Step 4: 配置 API Key
# =============================================================================
step "Step 4 / 配置 API Key"

# 4a. 创建 .env（幂等：已存在则跳过）
if [[ ! -f ".env" ]]; then
  cp .env.example .env
  ok "已从 .env.example 创建 .env"
fi

# 4b. 读取 key（优先从 .env 文件，其次系统环境变量）
_read_key() {
  local var_name="$1"
  local from_file=""
  if [[ -f ".env" ]]; then
    from_file="$(grep -E "^${var_name}=" .env 2>/dev/null | cut -d= -f2- | tr -d '[:space:]')"
  fi
  local from_env="${!var_name:-}"
  # 返回非 placeholder 的值
  local val="${from_file:-$from_env}"
  if [[ -n "$val" ]] && [[ "$val" != *"your-"* ]] && [[ "$val" != "your_"* ]]; then
    echo "$val"
  fi
}

MIMO_KEY="$(_read_key MIMO_API_KEY)"
DEEPSEEK_KEY="$(_read_key DEEPSEEK_API_KEY)"
HAS_KEY=false
[[ -n "$MIMO_KEY" ]]     && HAS_KEY=true && ok "MiMo API Key：已配置 (${MIMO_KEY:0:8}…)"
[[ -n "$DEEPSEEK_KEY" ]] && HAS_KEY=true && ok "DeepSeek API Key：已配置 (${DEEPSEEK_KEY:0:8}…)"

# 4c. 无有效 key 时交互式引导输入（幂等：已有 key 则整个块不执行）
if ! $HAS_KEY; then
  echo ""
  echo -e "${YELLOW}${BOLD}  需要配置 API Key 才能使用 Vico${RESET}"
  echo ""
  echo -e "  选择你的 Provider（只需填一个）："
  echo -e "  ${BOLD}1) MiMo${RESET}（默认）  —  https://platform.xiaomimimo.com"
  echo -e "     控制台 → Token Plan → 创建 API Key（格式：tp-xxxxx）"
  echo -e "  ${BOLD}2) DeepSeek${RESET}      —  https://platform.deepseek.com"
  echo -e "     API Keys → 创建（格式：sk-xxxxx）"
  echo ""

  if [[ -t 0 ]]; then
    # 交互式：循环直到填入至少一个有效 key
    while ! $HAS_KEY; do
      echo -ne "  请输入 MiMo API Key（留空跳过）: "
      read -r INPUT_MIMO
      if [[ -n "$INPUT_MIMO" ]] && [[ "$INPUT_MIMO" != *"your-"* ]]; then
        if grep -q "^MIMO_API_KEY=" .env 2>/dev/null; then
          sed -i.bak "s|^MIMO_API_KEY=.*|MIMO_API_KEY=${INPUT_MIMO}|" .env && rm -f .env.bak
        else
          echo "MIMO_API_KEY=${INPUT_MIMO}" >> .env
        fi
        ok "MiMo API Key 已写入 .env"
        HAS_KEY=true
        break
      fi

      echo -ne "  请输入 DeepSeek API Key（留空跳过）: "
      read -r INPUT_DEEPSEEK
      if [[ -n "$INPUT_DEEPSEEK" ]] && [[ "$INPUT_DEEPSEEK" != *"your-"* ]]; then
        if grep -q "^DEEPSEEK_API_KEY=" .env 2>/dev/null; then
          sed -i.bak "s|^DEEPSEEK_API_KEY=.*|DEEPSEEK_API_KEY=${INPUT_DEEPSEEK}|" .env && rm -f .env.bak
        else
          echo "DEEPSEEK_API_KEY=${INPUT_DEEPSEEK}" >> .env
        fi
        ok "DeepSeek API Key 已写入 .env"
        # 切换 .vicorc.json 默认 provider（幂等：用 python 原子改写）
        if command -v python3 &>/dev/null && [[ -f ".vicorc.json" ]]; then
          python3 - <<'PYEOF'
import json
with open('.vicorc.json') as f: cfg = json.load(f)
cfg.setdefault('llm', {}).setdefault('default', {})
cfg['llm']['default']['provider'] = 'deepseek'
cfg['llm']['default']['model']    = 'deepseek-v4-flash'
with open('.vicorc.json', 'w') as f: json.dump(cfg, f, indent=2, ensure_ascii=False)
print('   → .vicorc.json 已切换为 deepseek provider')
PYEOF
        fi
        HAS_KEY=true
        break
      fi

      warn "未输入任何有效 Key，请至少填写一个（Ctrl+C 退出后手动编辑 .env）"
      echo ""
    done
  else
    # 非交互环境（CI / 管道）
    warn "非交互环境，无法提示输入 Key"
    warn "请手动编辑 .env 文件，填入 MIMO_API_KEY 或 DEEPSEEK_API_KEY"
    warn "然后运行 'uv run vico' 启动"
    AUTO_LAUNCH=false
  fi
fi

# =============================================================================
# Step 5: （可选）全局安装
# =============================================================================
step "Step 5 / 全局安装"

if $GLOBAL_INSTALL; then
  # 检查是否已全局安装且版本一致（幂等：一致则跳过）
  LOCAL_VER="$(grep -E '^version\s*=' pyproject.toml | head -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')"
  INSTALLED_VER=""
  if command -v vico &>/dev/null; then
    # 尝试获取已安装版本（若 vico 不支持 --version 则为空）
    INSTALLED_VER="$(uv tool list 2>/dev/null | grep 'vico-ai-agent' | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true)"
  fi

  if [[ -n "$INSTALLED_VER" ]] && [[ "$INSTALLED_VER" == "$LOCAL_VER" ]]; then
    skip "全局 vico v${INSTALLED_VER}（已是最新）"
  else
    info "正在执行全局安装（uv tool install）..."
    uv tool install . --force --editable \
      && ok "全局安装完成，任意目录下运行 'vico' 即可启动" \
      || warn "全局安装失败，仍可用 'uv run vico' 在项目目录内启动"
  fi
else
  dim "已跳过全局安装（使用 --global 参数启用）"
  dim "全局安装后可在任意目录直接运行 vico 命令"
fi

# =============================================================================
# 完成汇总
# =============================================================================
echo ""
echo -e "${GREEN}${BOLD}═══════════════════════════════════════════${RESET}"
echo -e "${GREEN}${BOLD}  ✓  Vico AI Agent 已就绪！                ${RESET}"
echo -e "${GREEN}${BOLD}═══════════════════════════════════════════${RESET}"
echo ""
echo -e "  启动命令："
if $GLOBAL_INSTALL && command -v vico &>/dev/null; then
  echo -e "  ${BOLD}vico${RESET}              （已全局安装，任意目录可用）"
else
  echo -e "  ${BOLD}uv run vico${RESET}       （在项目目录内运行）"
fi
echo ""
echo -e "  常用命令："
echo -e "  ${DIM}/help${RESET}    — 显示帮助"
echo -e "  ${DIM}/model${RESET}   — 切换 Provider / 模型"
echo -e "  ${DIM}/clear${RESET}   — 清空对话历史"
echo -e "  ${DIM}/exit${RESET}    — 退出"
echo ""

# =============================================================================
# 自动启动（幂等：--no-launch 或无有效 key 时不启动）
# =============================================================================
if $AUTO_LAUNCH && $HAS_KEY; then
  echo -e "${CYAN}→${RESET}  正在启动 Vico...（Ctrl+C 可随时退出）"
  echo ""
  if $GLOBAL_INSTALL && command -v vico &>/dev/null; then
    exec vico
  else
    exec uv run vico
  fi
elif ! $HAS_KEY; then
  warn "未检测到有效的 API Key，请编辑 .env 文件后手动运行 'uv run vico'"
fi
