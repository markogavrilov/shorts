"""Auto-pick the top live streams in a Twitch category (Helix API, free app token)."""
import json
import time
import urllib.parse
import urllib.request

from .util import log

_token = {"value": None, "exp": 0}


def _http(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def _app_token(cid, secret):
    if _token["value"] and time.time() < _token["exp"] - 300:
        return _token["value"]
    body = urllib.parse.urlencode({"client_id": cid, "client_secret": secret,
                                   "grant_type": "client_credentials"}).encode()
    res = _http("https://id.twitch.tv/oauth2/token", data=body)
    _token.update(value=res["access_token"], exp=time.time() + res.get("expires_in", 3600))
    return _token["value"]


def top_streams(auto):
    """Returns list of (login, viewers, title)."""
    cid, secret = auto.get("client_id"), auto.get("client_secret")
    if not cid or not secret:
        raise RuntimeError("для [auto] нужны client_id и client_secret с dev.twitch.tv")
    h = {"Client-Id": cid, "Authorization": f"Bearer {_app_token(cid, secret)}"}
    game = auto.get("game_name", "Grand Theft Auto VI")
    g = _http("https://api.twitch.tv/helix/games?" + urllib.parse.urlencode({"name": game}),
              headers=h)["data"]
    if not g:
        raise RuntimeError(f"категория '{game}' не найдена на Twitch")
    q = {"game_id": g[0]["id"], "first": 100, "type": "live"}
    if auto.get("language"):
        q["language"] = auto["language"]
    streams = _http("https://api.twitch.tv/helix/streams?" + urllib.parse.urlencode(q),
                    headers=h)["data"]
    exclude = {x.lower() for x in auto.get("exclude", [])}
    min_v = int(auto.get("min_viewers", 0))
    out = [(s["user_login"].lower(), s["viewer_count"], s["title"]) for s in streams
           if s["user_login"].lower() not in exclude and s["viewer_count"] >= min_v]
    out.sort(key=lambda x: -x[1])
    return out


def pick(auto, taken=()):
    """Top `top_n` streams not already handled by another computer (`taken`)."""
    n = int(auto.get("top_n", 3))
    skip = int(auto.get("skip_top", 0))
    res = [s for s in top_streams(auto) if s[0] not in taken]
    chosen = res[skip:skip + n]
    log("auto", "топ сейчас: " + ", ".join(f"{l} ({v})" for l, v, _ in chosen))
    return [l for l, _, _ in chosen]
