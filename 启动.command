#!/bin/zsh
set -eu
cd -- "${0:A:h}"

for wb_python in "$PWD/.venv/bin/python3" \
  "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3" \
  /opt/homebrew/bin/python3 /usr/bin/python3; do
  if [[ -x "$wb_python" ]] && "$wb_python" -c 'import numpy, PIL' 2>/dev/null; then
    exec "$wb_python" launch.py
  fi
done

print '未找到含 NumPy 和 Pillow 的 Python。请按 README.md 中的便携安装步骤准备环境。'
print '没有自动安装任何软件。按回车关闭。'
read wb_reply
