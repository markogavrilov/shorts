"""Turns a raw 16:9 piece of stream into a vertical, edited and uniquified short.

Edit ("сочный монтаж"):
  * 9:16 layout (square / wide / facecam) over a blurred, darkened copy of the stream
  * snap zoom-out in the first 0.35 s (hook), slow push-in during the clip
  * punch-in zoom + camera shake + white flash + "boom" on the chat moment
  * whoosh at the start, hook caption in the first seconds
  * big word-by-word karaoke subtitles with the current word highlighted
  * progress bar, loudness normalised to -14 LUFS
Uniquify (random per variant): base zoom & pan, speed 1.02-1.06x, contrast/saturation/
brightness/gamma/hue, film grain, vignette, audio EQ, subtitle/hook/bar colours, hook text,
bitrate, stripped metadata.
"""
import json
import random
import subprocess
import time
from pathlib import Path

from .transcribe import transcribe_words
from .util import log, run, video_encoder_args

W, H = 1080, 1920
# ASS colours are &HBBGGRR
HIGHLIGHTS = ["&H0000E5FF", "&H0000FF7A", "&H00FFE100", "&H00FF4FD8", "&H0019A0FF"]
HOOK_BOXES = ["&H002A1AE8", "&H00000000", "&H00D9480F", "&H00008CFF"]
BAR_COLORS = ["0xFFD400", "0x00E676", "0xFF3D7F", "0x00B0FF", "0xFFFFFF"]


# ---------------------------------------------------------------- helpers
def probe(path):
    p = run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)])
    return json.loads(p.stdout)


def ass_time(t):
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def ass_escape(s):
    return s.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def wrap2(text, limit=16):
    if len(text) <= limit or " " not in text:
        return text
    mid = len(text) // 2
    spaces = [i for i, c in enumerate(text) if c == " "]
    cut = min(spaces, key=lambda i: abs(i - mid))
    return text[:cut] + r"\N" + text[cut + 1:]


def ensure_sfx(assets: Path):
    assets.mkdir(parents=True, exist_ok=True)
    whoosh, boom = assets / "whoosh.wav", assets / "boom.wav"
    if not whoosh.exists():
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
             "anoisesrc=d=0.55:c=pink:a=0.5:r=48000", "-af",
             "highpass=f=500,lowpass=f=5000,afade=t=in:d=0.22,afade=t=out:st=0.25:d=0.3,volume=0.8",
             "-ac", "2", str(whoosh)])
    if not boom.exists():
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
             "aevalsrc='0.9*sin(2*PI*(70-45*t)*t)*exp(-4.5*t)':s=48000:d=0.9",
             "-ac", "2", str(boom)])
    return whoosh, boom


# ---------------------------------------------------------------- variant params
def make_params(rng: random.Random, unique: bool, cfg, streamer):
    hooks = cfg["edit"].get("hook_texts") or ["ЭТО НАДО ВИДЕТЬ"]
    hook = rng.choice(hooks).replace("{streamer}", streamer.upper())
    if not unique:
        return dict(speed=1.0, zoom=1.0, panx=0.0, pany=0.0, contrast=1.05, saturation=1.15,
                    brightness=0.0, gamma=1.0, hue=0.0, grain=0, vignette=0.0, eq_f=1000,
                    eq_g=0.0, highlight=HIGHLIGHTS[0], hook_box=HOOK_BOXES[0],
                    bar=BAR_COLORS[0], hook=hook, bitrate=9, crf=20, drift=0.03)
    return dict(
        speed=round(rng.uniform(1.02, 1.06), 3),
        zoom=rng.uniform(1.03, 1.08),
        panx=rng.uniform(-0.015, 0.015), pany=rng.uniform(-0.015, 0.015),
        contrast=rng.uniform(1.03, 1.10), saturation=rng.uniform(1.08, 1.28),
        brightness=rng.uniform(-0.02, 0.03), gamma=rng.uniform(0.95, 1.05),
        hue=rng.uniform(-4, 4), grain=rng.randint(2, 4), vignette=rng.uniform(0.2, 0.45),
        eq_f=rng.choice([150, 300, 2500, 4000, 8000]), eq_g=rng.uniform(-2.5, 2.5),
        highlight=rng.choice(HIGHLIGHTS), hook_box=rng.choice(HOOK_BOXES),
        bar=rng.choice(BAR_COLORS), hook=hook, bitrate=rng.uniform(8, 10.5),
        crf=rng.choice([19, 20, 21]), drift=rng.uniform(0.02, 0.05))


# ---------------------------------------------------------------- subtitles
def build_ass(words, p, cfg, layout, moment_out, dur):
    font = cfg["edit"].get("font", "Arial Black")
    s = p["speed"]
    hook_y, sub_y = {"square": (200, 1610), "wide": (470, 1450), "facecam": (790, 1540)}[layout]
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}",
        "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Sub,{font},88,&H00FFFFFF,&H00FFFFFF,&H00000000,&H99000000,-1,0,0,0,100,100,0,0,1,7,4,5,40,40,0,1",
        f"Style: Hook,{font},78,&H00FFFFFF,&H00FFFFFF,{p['hook_box']},&H00000000,-1,0,0,0,100,100,0,0,3,18,0,5,40,40,0,1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    hook = wrap2(ass_escape(p["hook"].upper()))
    lines.append(f"Dialogue: 1,{ass_time(0)},{ass_time(2.7)},Hook,,0,0,0,,"
                 rf"{{\pos(540,{hook_y})\fscx60\fscy60\t(0,140,\fscx100\fscy100)\fad(0,180)}}{hook}")

    if words:
        # group into short chunks: <=3 words, <=16 chars, break on pauses
        chunks, cur = [], []
        for w in words:
            txt = ass_escape(w["word"]).upper().strip(" ,.")
            if not txt:
                continue
            w = dict(w, word=txt)
            if cur and (len(cur) >= 3 or sum(len(x["word"]) + 1 for x in cur) + len(txt) > 16
                        or w["start"] - cur[-1]["end"] > 0.5):
                chunks.append(cur)
                cur = []
            cur.append(w)
        if cur:
            chunks.append(cur)
        for ci, ch in enumerate(chunks):
            nxt = chunks[ci + 1][0]["start"] if ci + 1 < len(chunks) else ch[-1]["end"] + 0.6
            chunk_end = min(ch[-1]["end"] + 0.35, nxt)
            for wi, w in enumerate(ch):
                st = w["start"] if wi else min(w["start"], ch[0]["start"])
                en = ch[wi + 1]["start"] if wi + 1 < len(ch) else chunk_end
                if en <= st:
                    continue
                parts = []
                for wj, x in enumerate(ch):
                    if wj == wi:
                        parts.append(rf"{{\c{p['highlight']}\fscx112\fscy112}}{x['word']}{{\r}}")
                    else:
                        parts.append(x["word"])
                pop = r"\fscx80\fscy80\t(0,90,\fscx100\fscy100)" if wi == 0 else ""
                lines.append(f"Dialogue: 0,{ass_time(st / s)},{ass_time(en / s)},Sub,,0,0,0,,"
                             rf"{{\pos(540,{sub_y}){pop}}}" + " ".join(parts))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- filter graph
def build_filter(p, cfg, layout, moment_out, dur, has_audio):
    fps = cfg["edit"].get("fps", 30)
    s, z = p["speed"], p["zoom"]
    P, D = moment_out, dur
    T = f"(on/{fps})"
    zexpr = (f"1+0.22*max(0,1-{T}/0.35)"
             f"+{p['drift']:.3f}*{T}/{D:.2f}"
             f"+0.14*between({T},{P:.2f},{P + 1.6:.2f})*min(1,({T}-{P:.2f})/0.1)")
    shake = f"10*between({T},{P:.2f},{P + 0.45:.2f})*sin({T}*80)"

    def zp(w, h):
        return (f"zoompan=z='{zexpr}':x='iw/2-(iw/zoom/2)+{shake}':"
                f"y='ih/2-(ih/zoom/2)+{shake}*0.6':d=1:s={w}x{h}:fps={fps}")

    def base_crop(aspect_expr_w, aspect_expr_h):
        # crop with uniquifying zoom + pan, centred
        return (f"crop=w='{aspect_expr_w}/{z:.4f}':h='{aspect_expr_h}/{z:.4f}':"
                f"x='(iw-ow)/2+{p['panx']:.4f}*iw':y='(ih-oh)/2+{p['pany']:.4f}*ih'")

    f = [f"[0:v]setpts=(PTS-STARTPTS)/{s},fps={fps},split=2[a][b]",
         "[a]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=10:2,"
         "scale=1080:1920,eq=brightness=-0.13:saturation=1.25[bg]"]
    if layout == "square":
        f.append(f"[b]{base_crop('min(iw,ih)', 'min(iw,ih)')},scale=1080:1080,{zp(1080, 1080)}[fg]")
        f.append("[bg][fg]overlay=x=0:y=380[c]")
    elif layout == "wide":
        f.append(f"[b]{base_crop('iw', 'ih')},scale=1080:608,{zp(1080, 608)}[fg]")
        f.append("[bg][fg]overlay=x=0:y=656[c]")
    else:  # facecam
        fx, fy, fw, fh = cfg["edit"].get("facecam", [0.75, 0.7, 0.25, 0.3])
        f[0] = f"[0:v]setpts=(PTS-STARTPTS)/{s},fps={fps},split=3[a][b][cam]"
        f.append(f"[cam]crop=w=iw*{fw}:h=ih*{fh}:x=iw*{fx}:y=ih*{fy},"
                 "scale=1080:700:force_original_aspect_ratio=increase,crop=1080:700[camv]")
        f.append(f"[b]{base_crop('ih*1080/1220', 'ih')},scale=1080:1220,{zp(1080, 1220)}[game]")
        f.append("[camv][game]vstack=inputs=2[stk]")
        f.append("[bg][stk]overlay=0:0[c]")

    post = [f"eq=contrast={p['contrast']:.3f}:saturation={p['saturation']:.3f}:"
            f"brightness={p['brightness']:.3f}:gamma={p['gamma']:.3f}"]
    if abs(p["hue"]) > 0.01:
        post.append(f"hue=h={p['hue']:.2f}")
    if p["grain"]:
        post.append(f"noise=alls={p['grain']}:allf=t")
    if p["vignette"]:
        post.append(f"vignette=angle={p['vignette']:.3f}")
    post.append(f"drawbox=x=0:y=0:w=iw:h=ih:color=white@0.4:t=fill:"
                f"enable='between(t,{P:.2f},{P + 0.09:.2f})'")
    f.append("[c]" + ",".join(post) + "[d]")
    f.append(f"color=c={p['bar']}:s=1080x14:r={fps}[bar]")
    f.append(f"[d][bar]overlay=x='-w+w*t/{D:.2f}':y=H-14:shortest=1[e]")
    f.append("[e]ass=subs.ass,format=yuv420p[vout]")

    a_chain = (f"[0:a]asetpts=PTS-STARTPTS,atempo={s},"
               f"equalizer=f={p['eq_f']}:t=q:w=1.2:g={p['eq_g']:.2f},aresample=48000[a0]"
               if has_audio else f"anullsrc=r=48000:cl=stereo,atrim=0:{D:.2f}[a0]")
    f.append(a_chain)
    if cfg["edit"].get("sfx", True):
        f.append("[1:a]volume=0.55[wh]")
        f.append(f"[2:a]adelay={int(P * 1000)}|{int(P * 1000)},volume=0.9[bm]")
        f.append("[a0][wh][bm]amix=inputs=3:duration=first:normalize=0[am]")
    else:
        f.append("[a0]anull[am]")
    f.append("[am]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[aout]")
    return ";".join(f)


# ---------------------------------------------------------------- main entry
def render_clip(source: Path, offset: float, length: float, moment_in_clip: float,
                streamer: str, cfg, work_root: Path, stamp: str, extra_seed=0):
    """Render all unique variants. Returns list of output Paths."""
    t0 = time.time()
    out_root = Path(cfg["output_dir"])
    day = time.strftime("%Y-%m-%d")
    base = f"{streamer}_{stamp}"
    job = work_root / "jobs" / base
    job.mkdir(parents=True, exist_ok=True)
    layout = cfg["edit"].get("layout", "square")
    if layout not in ("square", "wide", "facecam"):
        layout = "square"

    info = probe(source)
    has_audio = any(st.get("codec_type") == "audio" for st in info["streams"])

    # 1) raw 16:9 copy for manual editing
    raw_dir = out_root / "raw" / day
    raw_dir.mkdir(parents=True, exist_ok=True)
    try:
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{offset:.2f}",
             "-t", f"{length:.2f}", "-i", str(source), "-c", "copy",
             "-movflags", "+faststart", str(raw_dir / f"{base}.mp4")])
    except RuntimeError as e:
        log(streamer, f"сырой клип не сохранился: {str(e)[-200:]}")

    # 2) speech -> words
    words = []
    if cfg["edit"].get("subtitles", True) and has_audio:
        wav = job / "audio.wav"
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{offset:.2f}",
             "-t", f"{length:.2f}", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", str(wav)])
        try:
            words = transcribe_words(wav, cfg)
        except Exception as e:
            log(streamer, f"субтитры не получились: {e}")
    t_sub = time.time() - t0

    whoosh, boom = ensure_sfx(work_root / "assets")
    uq = cfg.get("unique", {})
    unique = uq.get("enabled", True)
    variants = max(1, int(uq.get("variants", 1))) if unique else 1
    out_dir = out_root / ("unique" if unique else "edited") / day
    out_dir.mkdir(parents=True, exist_ok=True)

    outputs = []
    for vi in range(1, variants + 1):
        rng = random.Random(hash((base, vi, extra_seed, time.time())))
        p = make_params(rng, unique, cfg, streamer)
        dur = length / p["speed"]
        moment_out = moment_in_clip / p["speed"]
        (job / "subs.ass").write_text(build_ass(words, p, cfg, layout, moment_out, dur),
                                      encoding="utf-8")
        fg = build_filter(p, cfg, layout, moment_out, dur, has_audio)
        enc = video_encoder_args()
        if "h264_videotoolbox" in enc:
            br = p["bitrate"]
            enc[enc.index("-b:v") + 1] = f"{br:.1f}M"
        elif "-crf" in enc:
            enc[enc.index("-crf") + 1] = str(p["crf"])
        out = out_dir / f"{base}_v{vi}.mp4"
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
               "-ss", f"{offset:.2f}", "-t", f"{length:.2f}", "-i", str(source),
               "-i", str(whoosh), "-i", str(boom),
               "-filter_complex", fg, "-map", "[vout]", "-map", "[aout]",
               "-t", f"{dur:.2f}", *enc, "-g", str(cfg["edit"].get("fps", 30) * 2),
               "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
               "-map_metadata", "-1", "-fflags", "+bitexact",
               "-metadata", f"creation_time={time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime())}Z",
               "-movflags", "+faststart", str(out)]
        run(cmd, cwd=job)
        outputs.append(out)
    log(streamer, f"готово за {time.time() - t0:.0f} с (субтитры {t_sub:.0f} с, "
                  f"слов: {len(words)}): {', '.join(o.name for o in outputs)}")
    if not cfg.get("keep_jobs"):
        for fpath in job.iterdir():
            fpath.unlink(missing_ok=True)
        job.rmdir()
    return outputs
