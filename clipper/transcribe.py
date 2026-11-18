"""Word-level speech recognition.
Apple Silicon (M1-M5): mlx-whisper on the GPU.  Intel Mac / other: faster-whisper on CPU.
"""
import threading

from .util import is_apple_silicon, log

_lock = threading.Lock()
_model = None
_backend = None


def _pick(cfg_model):
    if is_apple_silicon():
        try:
            import mlx_whisper  # noqa: F401
            name = cfg_model if cfg_model != "auto" else "mlx-community/whisper-large-v3-turbo"
            return "mlx", name
        except ImportError:
            pass
    name = cfg_model if cfg_model != "auto" else "small"
    return "faster", name


def transcribe_words(wav_path, cfg):
    """Returns list of {"word", "start", "end"} (seconds)."""
    global _model, _backend
    e = cfg["edit"]
    lang = e.get("language", "ru")
    lang = None if lang == "auto" else lang
    with _lock:
        if _backend is None:
            _backend, name = _pick(e.get("whisper_model", "auto"))
            log("whisper", f"модель: {name} ({_backend})")
            if _backend == "mlx":
                _model = name
            else:
                from faster_whisper import WhisperModel
                _model = WhisperModel(name, device="cpu", compute_type="int8")
        words = []
        if _backend == "mlx":
            import mlx_whisper
            res = mlx_whisper.transcribe(str(wav_path), path_or_hf_repo=_model,
                                         word_timestamps=True, language=lang,
                                         condition_on_previous_text=False)
            for seg in res.get("segments", []):
                for w in seg.get("words", []):
                    words.append({"word": w["word"].strip(), "start": w["start"], "end": w["end"]})
        else:
            segs, _ = _model.transcribe(str(wav_path), word_timestamps=True, language=lang,
                                        vad_filter=True, condition_on_previous_text=False)
            for seg in segs:
                for w in seg.words or []:
                    words.append({"word": w.word.strip(), "start": w.start, "end": w.end})
    return [w for w in words if w["word"]]
