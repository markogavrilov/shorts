"""Live Twitch clipper.

  python -m clipper                       следить за каналами из config.toml
  python -m clipper --test-file video.mp4 --moment 40
                                          собрать клип из файла (момент на 40-й секунде)
  python -m clipper --chat-test <канал>   показать активность чата (без записи)
  python -m clipper --telegram-setup      узнать chat id для Telegram
"""
import argparse
import queue
import sys
import threading
import time
from pathlib import Path

from .chat import ChatReader
from .editor import render_clip
from .recorder import Recorder
from .util import ROOT, load_config, log, need_binary
from . import telegram


def main():
    ap = argparse.ArgumentParser(description="Живой нарезчик Twitch")
    ap.add_argument("--config", default=None)
    ap.add_argument("--test-file")
    ap.add_argument("--moment", type=float, default=None,
                    help="секунда момента в тестовом файле")
    ap.add_argument("--chat-test")
    ap.add_argument("--telegram-setup", action="store_true")
    args = ap.parse_args()
    cfg = load_config(args.config)
    work = ROOT / ".work"
    work.mkdir(exist_ok=True)

    if args.telegram_setup:
        telegram.setup(cfg)
        return
    need_binary("ffmpeg")
    need_binary("ffprobe")

    if args.chat_test:
        cfg["detect"]["warmup_seconds"] = 30
        r = ChatReader(args.chat_test, cfg, lambda s: log(
            args.chat_test, f"*** МОМЕНТ! {s['count']} сообщ. (обычно {s['baseline']:.0f}), "
                            f"x{s['score']:.1f}, слова: {', '.join(s['top'])}"), verbose=True)
        r.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            return

    if args.test_file:
        test_file(args, cfg, work)
        return

    need_binary("streamlink")
    run_live(cfg, work)


def clip_window(cfg):
    c = cfg["clip"]
    length = float(c.get("length_seconds", 30))
    after = float(c.get("after_moment_seconds", 8))
    return length, after, float(c.get("reaction_delay_seconds", 6))


def test_file(args, cfg, work):
    from .editor import probe
    src = Path(args.test_file).resolve()
    dur = float(probe(src)["format"]["duration"])
    length, after, _ = clip_window(cfg)
    length = min(length, dur)
    moment = args.moment if args.moment is not None else dur / 2
    start = max(0.0, min(moment - (length - after), dur - length))
    stamp = time.strftime("%H%M%S")
    outs = render_clip(src, start, length, moment - start, "test", cfg, work, stamp)
    for o in outs:
        print(o)
    if cfg["telegram"].get("enabled"):
        for o in outs:
            telegram.send_video(cfg, o, "Тестовый клип")


def run_live(cfg, work):
    channels = [c.strip().lower() for c in cfg.get("channels", []) if c.strip()]
    if not channels:
        sys.exit("В config.toml не указаны каналы (channels = [...])")
    length, after, reaction = clip_window(cfg)
    jobs: queue.Queue = queue.Queue()
    recorders = {}

    def make_handler(ch):
        def on_spike(s):
            rec = recorders[ch]
            if not rec.live:
                return
            moment = s["time"] - reaction
            log(ch, f"МОМЕНТ! чат x{s['score']:.1f} ({s['count']} сообщ., обычно "
                    f"{s['baseline']:.0f}) -> режу клип")
            jobs.put((ch, moment, s))
        return on_spike

    for ch in channels:
        rec = Recorder(ch, cfg, work)
        recorders[ch] = rec
        rec.start()
        ChatReader(ch, cfg, make_handler(ch)).start()

    def worker():
        while True:
            ch, moment, s = jobs.get()
            try:
                rec = recorders[ch]
                wall_from, wall_to = moment - (length - after), moment + after
                deadline = time.time() + 60
                while rec.latest_wall() < wall_to and time.time() < deadline:
                    time.sleep(0.5)
                stamp = time.strftime("%H%M%S", time.localtime(moment))
                src = work / "jobs" / f"src_{ch}_{stamp}.ts"
                src.parent.mkdir(parents=True, exist_ok=True)
                offset = rec.extract(wall_from, wall_to, src)
                if offset is None:
                    log(ch, "нет записанного видео для этого момента, пропускаю")
                    continue
                outs = render_clip(src, offset, length, length - after, ch, cfg, work, stamp)
                src.unlink(missing_ok=True)
                if cfg["telegram"].get("enabled"):
                    cap = (f"twitch.tv/{ch} · {time.strftime('%H:%M:%S', time.localtime(moment))}\n"
                           f"чат x{s['score']:.1f} · {', '.join(s['top'][:4])}")
                    for o in outs:
                        telegram.send_video(cfg, o, cap)
            except Exception as e:
                log(ch, f"ошибка сборки клипа: {e}")
            finally:
                jobs.task_done()

    for _ in range(max(1, int(cfg.get("render_workers", 1)))):
        threading.Thread(target=worker, daemon=True).start()

    log("main", f"слежу за: {', '.join(channels)}. Клипы: {cfg['output_dir']}. "
                "Остановить: Ctrl+C")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log("main", "остановлено")


if __name__ == "__main__":
    main()
