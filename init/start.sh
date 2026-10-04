#!/usr/bin/env bash
# YouTube Downloader 一键启动脚本（Linux / macOS）
#
# 用法（在仓库根目录执行）：
#   bash init/start.sh                # 单端口模式（默认）：安装依赖 → 构建前端 → 启动后端，由后端在同一端口托管页面与 API
#   bash init/start.sh dev            # 开发模式：后端（--reload）与前端 Vite dev server 并发运行
#   bash init/start.sh --no-build     # 单端口模式但跳过前端构建（仅改了后端、想快速重启时）
#   bash init/start.sh --auto-port    # 单端口模式，端口被占用时自动往后找（透传给 python -m app）
#
# 任意未知参数都会原样透传给 `python -m app`，例如：
#   bash init/start.sh --port 8010
#   bash init/start.sh dev --port 8010   （注意：dev 模式自定义端口需同时改 .env 的 YTDL_API_PORT，否则 Vite 代理仍指向旧端口）
#
# 前置依赖：Node.js 20+、Python 3.12+、npm；ffmpeg 可选（缺失时后端用 imageio-ffmpeg 兜底）。
# 若仓库根或 backend/ 下已存在 .venv，脚本会自动使用；否则使用 PATH 中的 python3 / python。

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FRONTEND_DIR="$REPO_ROOT/frontend"
BACKEND_DIR="$REPO_ROOT/backend"

MODE="prod"
NO_BUILD=0
BACKEND_EXTRA=()

while [ $# -gt 0 ]; do
  case "$1" in
    dev) MODE="dev" ;;
    --no-build) NO_BUILD=1 ;;
    -h|--help)
      sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) BACKEND_EXTRA+=("$1") ;;   # 其余参数（--port / --auto-port / --reload / --host 等）透传给后端
  esac
  shift
done

err() { echo "错误：$1" >&2; exit 1; }

# ---- 前端依赖检查 ----
command -v node >/dev/null 2>&1 || err "未找到 node。请先安装 Node.js 20+（https://nodejs.org）。"
command -v npm  >/dev/null 2>&1 || err "未找到 npm。请先安装 Node.js（npm 随 Node 一起提供）。"
NODE_VER="$(node -v | sed 's/^v//')"
NODE_MAJOR="${NODE_VER%%.*}"
[ "$NODE_MAJOR" -ge 20 ] || err "Node.js 版本过低（当前 v$NODE_VER），需要 20+。"

# ---- 选择 Python 解释器 ----
PYTHON=""
if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
  PYTHON="$VIRTUAL_ENV/bin/python"
elif [ -x "$REPO_ROOT/.venv/bin/python" ]; then
  PYTHON="$REPO_ROOT/.venv/bin/python"
elif [ -x "$BACKEND_DIR/.venv/bin/python" ]; then
  PYTHON="$BACKEND_DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="python3"
elif command -v python  >/dev/null 2>&1; then
  PYTHON="python"
else
  err "未找到 Python 3.12+。请先安装 Python（https://www.python.org）。"
fi

PY_VER="$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "0.0")"
PY_MAJOR="${PY_VER%%.*}"
PY_MINOR="$(echo "$PY_VER" | cut -d. -f2)"
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "${PY_MINOR:-0}" -lt 12 ]; }; then
  err "Python 版本过低（当前 $PY_VER），需要 3.12+。"
fi
echo "使用 Python：$PYTHON ($PY_VER)"

# ---- 前端依赖与构建 ----
if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
  echo "==> 安装前端依赖（npm install）..."
  (cd "$FRONTEND_DIR" && npm install) || err "前端依赖安装失败。"
fi

if [ "$MODE" = "prod" ] && [ "$NO_BUILD" -eq 0 ]; then
  echo "==> 构建前端（npm run build）..."
  (cd "$FRONTEND_DIR" && npm run build) || err "前端构建失败。"
fi

# ---- 后端依赖 ----
echo "==> 安装后端依赖（pip install -e .）..."
(cd "$BACKEND_DIR" && "$PYTHON" -m pip install -e .) || err "后端依赖安装失败。"

# ---- 启动 ----
if [ "$MODE" = "dev" ]; then
  echo ""
  echo "==> 开发模式：并发启动后端（--reload）与前端 Vite dev server"
  echo "    后端地址：http://127.0.0.1:${YTDL_API_PORT:-8000}（可被仓库根 .env 的 YTDL_API_PORT 覆盖）"
  echo "    前端地址：http://127.0.0.1:5173"
  echo "    按 Ctrl+C 同时停止两者。"
  echo ""

  (cd "$BACKEND_DIR" && "$PYTHON" -m app --reload "${BACKEND_EXTRA[@]}") &
  BACKEND_PID=$!
  (cd "$FRONTEND_DIR" && npm run dev -- --port 5173) &
  FRONTEND_PID=$!

  cleanup() {
    echo ""
    echo "==> 正在停止前后端..."
    kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
    wait "$BACKEND_PID" 2>/dev/null || true
    wait "$FRONTEND_PID" 2>/dev/null || true
  }
  trap cleanup EXIT INT TERM

  wait
else
  echo ""
  echo "==> 单端口模式：启动后端（由它同时托管前端页面与 API）"
  echo "    启动后请打开后端打印的「服务地址」。默认 http://127.0.0.1:8000"
  echo "    按 Ctrl+C 停止。"
  echo ""

  if [ ${#BACKEND_EXTRA[@]} -eq 0 ]; then
    (cd "$BACKEND_DIR" && "$PYTHON" -m app)
  else
    (cd "$BACKEND_DIR" && "$PYTHON" -m app "${BACKEND_EXTRA[@]}")
  fi
fi
