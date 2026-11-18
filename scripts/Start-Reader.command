#!/bin/sh
# macOS 双击入口与 Linux 使用同一个启动脚本
set -eu
reader_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec /bin/sh "$reader_root/Start-Reader.sh" "$@"
