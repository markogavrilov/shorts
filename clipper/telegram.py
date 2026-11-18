import json
import urllib.request
import uuid
from pathlib import Path

from .util import log


def _api(token, method, data=None, files=None, timeout=120):
    url = f"https://api.telegram.org/bot{token}/{method}"
    if files:
        boundary = uuid.uuid4().hex
        body = b""
        for k, v in (data or {}).items():
            body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n"
                     f"{v}\r\n").encode()
        for k, path in files.items():
            body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; "
                     f"filename=\"{Path(path).name}\"\r\nContent-Type: video/mp4\r\n\r\n").encode()
            body += Path(path).read_bytes() + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        req = urllib.request.Request(url, data=body, headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}"})
    else:
        req = urllib.request.Request(url, data=json.dumps(data or {}).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def send_video(cfg, path, caption=""):
    tg = cfg["telegram"]
    if not tg.get("bot_token") or not tg.get("chat_id"):
        log("telegram", "не задан bot_token или chat_id")
        return
    if Path(path).stat().st_size > 49 * 1024 * 1024:
        log("telegram", f"{Path(path).name} больше 50 МБ, Telegram не примет — он в папке")
        return
    try:
        _api(tg["bot_token"], "sendVideo",
             {"chat_id": tg["chat_id"], "caption": caption[:1000], "supports_streaming": "true"},
             {"video": path})
        log("telegram", f"отправлено: {Path(path).name}")
    except Exception as e:
        log("telegram", f"не отправилось: {e}")


def setup(cfg):
    token = cfg["telegram"].get("bot_token")
    if not token:
        raise SystemExit("Сначала впишите bot_token в config.toml (из @BotFather).")
    res = _api(token, "getUpdates")
    chats = {}
    for u in res.get("result", []):
        msg = u.get("message") or u.get("channel_post") or {}
        ch = msg.get("chat")
        if ch:
            chats[ch["id"]] = ch.get("username") or ch.get("title") or ch.get("first_name")
    if not chats:
        print("Бот пока не получил сообщений. Напишите ему /start в Telegram и запустите снова.")
    for cid, name in chats.items():
        print(f"chat_id = \"{cid}\"   ({name})")
