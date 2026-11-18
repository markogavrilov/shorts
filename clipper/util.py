import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_print_lock = threading.Lock()


def log(tag, msg):
    with _print_lock:
        print(f"{time.strftime('%H:%M:%S')} [{tag}] {msg}", flush=True)


def load_config(path=None):
    path = Path(path) if path else ROOT / "config.toml"
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    tg = cfg.setdefault("telegram", {})
    tg["bot_token"] = os.environ.get("TELEGRAM_BOT_TOKEN") or tg.get("bot_token", "")
    tg["chat_id"] = os.environ.get("TELEGRAM_CHAT_ID") or str(tg.get("chat_id", ""))
    out = Path(cfg.get("output_dir", "clips"))
    cfg["output_dir"] = out if out.is_absolute() else ROOT / out
    return cfg


def is_apple_silicon():
    return sys.platform == "darwin" and platform.machine() == "arm64"


def need_binary(name):
    if not shutil.which(name):
        raise SystemExit(f"Не найдена программа '{name}'. Запустите setup_mac.sh (brew install {name}).")


def run(cmd, **kw):
    """Run a command, raise with stderr tail on failure."""
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)
    if p.returncode != 0:
        tail = p.stderr.decode(errors="replace")[-2500:]
        raise RuntimeError(f"Команда упала: {' '.join(map(str, cmd[:4]))} ...\n{tail}")
    return p


_encoder = None


def video_encoder_args():
    """Hardware encoder on Mac (VideoToolbox), libx264 elsewhere / as fallback."""
    global _encoder
    if _encoder is None:
        _encoder = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                    "-maxrate", "10M", "-bufsize", "20M"]
        if sys.platform == "darwin":
            test = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                 "color=black:s=320x240:d=0.2", "-c:v", "h264_videotoolbox", "-f", "null", "-"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if test.returncode == 0:
                _encoder = ["-c:v", "h264_videotoolbox", "-b:v", "9M", "-maxrate", "12M",
                            "-bufsize", "18M", "-profile:v", "high"]
    return list(_encoder)
