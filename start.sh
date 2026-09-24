#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
export UV_CACHE_DIR="$PWD/.uv-cache"
echo "[1/3] 检查 uv..."

if command -v uv >/dev/null 2>&1; then
    uv_bin="$(command -v uv)"
elif [[ -x "${HOME}/.local/bin/uv" ]]; then
    uv_bin="${HOME}/.local/bin/uv"
elif [[ -x "${HOME}/.cargo/bin/uv" ]]; then
    uv_bin="${HOME}/.cargo/bin/uv"
else
    echo "[1/3] 正在安装 uv..."
    export UV_INSTALL_DIR="${HOME}/.local/bin"
    if command -v curl >/dev/null 2>&1; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
    elif command -v wget >/dev/null 2>&1; then
        wget -qO- https://astral.sh/uv/install.sh | sh
    else
        echo "安装 uv 需要 curl 或 wget。" >&2
        exit 1
    fi
    if [[ -x "${HOME}/.local/bin/uv" ]]; then
        uv_bin="${HOME}/.local/bin/uv"
    elif [[ -x "${HOME}/.cargo/bin/uv" ]]; then
        uv_bin="${HOME}/.cargo/bin/uv"
    else
        echo "uv 安装后未找到可执行文件。" >&2
        exit 1
    fi
fi

echo "[2/3] 正在准备 Python 3.12 环境并下载依赖..."
"${uv_bin}" sync --locked

echo "[3/3] 启动德州扑克服务：http://localhost:8765/"
exec .venv/bin/python server.py
