#!/bin/zsh
set -eu
cd -- "${0:A:h}"
print '开发版 / 背景校正功能测试 · v1.1.0-dev · 端口 8766'
if [[ ! -x "$PWD/.venv/bin/python3" ]]; then
  print '开发版独立环境缺失。请按开发说明恢复；不会借用标准版环境。'
  exit 1
fi
mkdir -p "$PWD/logs"
exec "$PWD/.venv/bin/python3" launch.py 2>> "$PWD/logs/server-errors.log"
