#!/bin/bash
# 启动乐谱工作室 (score_studio)
cd "$(dirname "$0")"

if [ -x "venv/bin/python" ]; then
    PY="venv/bin/python"
else
    PY="python3"
fi

exec "$PY" score_studio.py "$@"
