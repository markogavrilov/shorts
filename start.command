#!/bin/bash
# Двойной клик в Finder = запуск нарезчика. Остановить: Ctrl+C или закрыть окно.
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
FULL="$(brew --prefix ffmpeg-full 2>/dev/null)/bin"
[ -x "$FULL/ffmpeg" ] && export PATH="$FULL:$PATH"
export PATH="$(pwd)/.venv/bin:$PATH"
exec .venv/bin/python -m clipper "$@"
