"""Continuous Twitch recording into a rolling buffer of short .ts segments.

streamlink -> stdout -> ffmpeg (-c copy) -> 2-second segments + csv index.
Each segment gets an estimated wall-clock start so chat timestamps can be mapped
onto video.
"""
import shutil
import subprocess
import threading
import time
from pathlib import Path

from .util import log


class Segment:
    __slots__ = ("path", "wall_start", "duration")

    def __init__(self, path, wall_start, duration):
        self.path, self.wall_start, self.duration = path, wall_start, duration

    @property
    def wall_end(self):
        return self.wall_start + self.duration


class Recorder(threading.Thread):
    def __init__(self, channel, cfg, work_dir: Path):
        super().__init__(daemon=True, name=f"rec-{channel}")
        self.channel = channel
        self.quality = cfg["clip"].get("quality", "best")
        self.buffer_seconds = cfg["clip"].get("buffer_seconds", 240)
        self.dir = work_dir / "buffer" / channel
        self.segments: list[Segment] = []
        self.lock = threading.Lock()
        self.live = False
        self._stop = threading.Event()

    # ---------- public ----------
    def stop(self):
        self._stop.set()

    def latest_wall(self):
        with self.lock:
            return self.segments[-1].wall_end if self.segments else 0

    def extract(self, wall_from, wall_to, dest: Path):
        """Concatenate segments covering [wall_from, wall_to] into dest (.ts).
        Returns offset (seconds) of wall_from inside dest, or None."""
        with self.lock:
            segs = [s for s in self.segments if s.wall_end > wall_from and s.wall_start < wall_to]
        segs = [s for s in segs if s.path.exists()]
        if not segs:
            return None
        with open(dest, "wb") as out:
            for s in segs:
                with open(s.path, "rb") as f:
                    shutil.copyfileobj(f, out)
        return max(0.0, wall_from - segs[0].wall_start)

    # ---------- internals ----------
    def run(self):
        while not self._stop.is_set():
            try:
                self._record_once()
            except Exception as e:  # keep going no matter what
                log(self.channel, f"ошибка записи: {e}")
            self.live = False
            if self._stop.wait(30):
                break

    def _record_once(self):
        if self.dir.exists():
            shutil.rmtree(self.dir, ignore_errors=True)
        self.dir.mkdir(parents=True, exist_ok=True)
        with self.lock:
            self.segments = []
        csv = self.dir / "list.csv"
        sl = subprocess.Popen(
            ["streamlink", "--twitch-low-latency", "--twitch-disable-ads", "--stdout",
             "--loglevel", "error", f"https://twitch.tv/{self.channel}", self.quality],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        ff = subprocess.Popen(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
             "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
             "-f", "segment", "-segment_time", "2", "-segment_format", "mpegts",
             "-segment_list", str(csv), "-segment_list_type", "csv",
             "-segment_list_flags", "+live",
             str(self.dir / "seg_%07d.ts")],
            stdin=sl.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        sl.stdout.close()

        seen_lines = 0
        anchor = None  # wall_time - stream_time
        started = time.time()
        try:
            while not self._stop.is_set():
                if ff.poll() is not None:
                    break
                time.sleep(0.5)
                if not csv.exists():
                    if time.time() - started > 40 and not self.live:
                        break  # offline
                    continue
                lines = csv.read_text().strip().splitlines()
                now = time.time()
                for line in lines[seen_lines:]:
                    parts = line.split(",")
                    if len(parts) < 3:
                        continue
                    name, st, en = parts[0], float(parts[1]), float(parts[2])
                    est = now - en
                    # smooth anchor: segments arrive in bursts, keep the earliest-plausible
                    anchor = est if anchor is None else min(anchor + 0.02, max(anchor - 1.0, est))
                    seg = Segment(self.dir / name, anchor + st, en - st)
                    with self.lock:
                        self.segments.append(seg)
                    if not self.live:
                        self.live = True
                        log(self.channel, "стрим в эфире, запись идёт")
                seen_lines = len(lines)
                self._cleanup()
        finally:
            for p in (ff, sl):
                if p.poll() is None:
                    p.terminate()
                    try:
                        p.wait(5)
                    except subprocess.TimeoutExpired:
                        p.kill()
        if self.live:
            log(self.channel, "стрим закончился или оборвался, переподключаюсь")
        else:
            err = (sl.stderr.read() or b"").decode(errors="replace").strip().splitlines()
            reason = err[-1] if err else "оффлайн"
            log(self.channel, f"не в эфире ({reason[:120]}), проверю снова через 30 с")

    def _cleanup(self):
        limit = time.time() - self.buffer_seconds
        with self.lock:
            old = [s for s in self.segments if s.wall_end < limit]
            self.segments = [s for s in self.segments if s.wall_end >= limit]
        for s in old:
            try:
                s.path.unlink()
            except FileNotFoundError:
                pass
