"""Anonymous Twitch chat reader + chat-spike detector."""
import re
import socket
import ssl
import statistics
import threading
import time
from collections import Counter, deque

from .util import log

HYPE = re.compile(
    r"(kekw|lul|lmao|lmfao|omegalul|icant|pog|poggers|pogchamp|monkas|wtf|xd|clip it|clip that|"
    r"clipped|no way|nah+|bro+|holy|insane|crazy|w+ |💀|😭|🤣|😂|‼|"
    r"ахах|хаха|лол|кек|жесть|ору|!!!|\?\?\?)",
    re.IGNORECASE)


class SpikeDetector:
    def __init__(self, cfg):
        d = cfg["detect"]
        self.window = d.get("window_seconds", 5)
        self.base_len = int(d.get("baseline_minutes", 5) * 60)
        self.ratio = d.get("spike_ratio", 3.0)
        self.min_msgs = d.get("min_messages", 12)
        self.cooldown = d.get("cooldown_seconds", 60)
        self.warmup = d.get("warmup_seconds", 90)
        self.msgs = deque()           # (ts, text)
        self.history = deque()        # (ts, window_count) sampled every second
        self.started = time.time()
        self.last_trigger = 0.0
        self.last_sample = 0.0

    def add(self, ts, text):
        self.msgs.append((ts, text))

    def tick(self, now):
        """Call ~every second. Returns dict with spike info or None."""
        while self.msgs and self.msgs[0][0] < now - self.base_len:
            self.msgs.popleft()
        if now - self.last_sample < 1.0:
            return None
        self.last_sample = now
        recent = [t for ts, t in self.msgs if ts >= now - self.window]
        count = len(recent)
        past = [c for ts, c in self.history if ts < now - self.window]
        baseline = statistics.median(past) if len(past) >= 20 else None
        self.history.append((now, count))
        while self.history and self.history[0][0] < now - self.base_len:
            self.history.popleft()
        if baseline is None or now - self.started < self.warmup:
            return None
        hype = sum(1 for t in recent if HYPE.search(t)) / count if count else 0
        effective = count * (1 + 0.5 * hype)
        score = effective / max(baseline, 1.5)
        if (score >= self.ratio and count >= self.min_msgs
                and now - self.last_trigger >= self.cooldown):
            self.last_trigger = now
            words = Counter(w.lower() for t in recent for w in t.split() if len(w) > 2)
            return {"time": now, "count": count, "baseline": baseline, "score": score,
                    "hype": hype, "top": [w for w, _ in words.most_common(5)]}
        return None


class ChatReader(threading.Thread):
    """Reads chat anonymously (justinfan) and calls on_spike(info)."""

    def __init__(self, channel, cfg, on_spike, verbose=False):
        super().__init__(daemon=True, name=f"chat-{channel}")
        self.channel = channel.lower()
        self.detector = SpikeDetector(cfg)
        self.on_spike = on_spike
        self.verbose = verbose
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        while not self._stop.is_set():
            try:
                self._loop()
            except Exception as e:
                log(self.channel, f"чат отвалился ({e}), переподключаюсь")
            self._stop.wait(5)

    def _loop(self):
        raw = socket.create_connection(("irc.chat.twitch.tv", 6697), timeout=10)
        sock = ssl.create_default_context().wrap_socket(raw, server_hostname="irc.chat.twitch.tv")
        sock.settimeout(1.0)
        nick = f"justinfan{int(time.time()) % 100000}"
        sock.sendall(f"PASS SCHMOOPIIE\r\nNICK {nick}\r\nJOIN #{self.channel}\r\n".encode())
        log(self.channel, "подключился к чату")
        buf = b""
        last_print = time.time()
        while not self._stop.is_set():
            try:
                data = sock.recv(65536)
                if not data:
                    raise ConnectionError("соединение закрыто")
                buf += data
            except (socket.timeout, TimeoutError):
                pass
            now = time.time()
            *lines, buf = buf.split(b"\r\n")
            for line in lines:
                s = line.decode("utf-8", errors="replace")
                if s.startswith("PING"):
                    sock.sendall(s.replace("PING", "PONG", 1).encode() + b"\r\n")
                elif " PRIVMSG " in s:
                    self.detector.add(now, s.split(" :", 1)[-1] if " :" in s else "")
            spike = self.detector.tick(now)
            if spike:
                self.on_spike(spike)
            if self.verbose and now - last_print >= 5:
                last_print = now
                h = self.detector.history
                cur = h[-1][1] if h else 0
                log(self.channel, f"сообщений за {self.detector.window}с: {cur}")
        sock.close()
