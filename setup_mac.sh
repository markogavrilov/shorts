#!/bin/bash
# Установка нарезчика на Mac. Запуск:  bash setup_mac.sh
set -e
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

if ! command -v brew >/dev/null; then
  echo ">>> Ставлю Homebrew (попросит пароль от Mac)"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
fi

echo ">>> Ставлю ffmpeg и Python"
brew install ffmpeg python@3.12
# Субтитрам нужен ffmpeg с libass. Если в обычном его нет — ставим полную сборку.
if ! ffmpeg -hide_banner -filters 2>/dev/null | grep -q " ass "; then
  echo ">>> В ffmpeg нет субтитров (libass), ставлю ffmpeg-full"
  brew install ffmpeg-full
fi

PY="$(brew --prefix python@3.12)/bin/python3.12"
"$PY" -m venv .venv
.venv/bin/pip install -q -U pip
.venv/bin/pip install -q streamlink
if [ "$(uname -m)" = "arm64" ]; then
  echo ">>> Apple Silicon: распознавание речи на GPU (mlx-whisper)"
  .venv/bin/pip install -q mlx-whisper
else
  echo ">>> Intel Mac: распознавание речи на процессоре (faster-whisper)"
  .venv/bin/pip install -q faster-whisper
fi

echo ">>> Скачиваю модель распознавания речи (один раз, ~1-1.5 ГБ)"
ffmpeg -hide_banner -loglevel error -y -f lavfi -i "sine=f=300:d=2" -ar 16000 -ac 1 .warmup.wav
.venv/bin/python -c "
from clipper.util import load_config
from clipper.transcribe import transcribe_words
transcribe_words('.warmup.wav', load_config())
print('модель готова')"
rm -f .warmup.wav
chmod +x start.command
echo ""
echo "ГОТОВО. Впишите каналы в config.toml и запустите start.command (двойной клик)."
