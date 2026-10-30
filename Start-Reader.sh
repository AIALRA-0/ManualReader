#!/bin/sh
# 在本目录建立独立环境，首次安装依赖后启动阅读器，不修改系统 Python
set -eu
reader_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
reader_python=${MANUAL_READER_PYTHON:-python3}
if ! command -v "$reader_python" >/dev/null 2>&1; then
    printf '%s\n' '请先安装 Python 3.10 或以上版本，再运行本脚本' >&2
    exit 1
fi
exec "$reader_python" "$reader_root/bootstrap_reader.py" "$@"
