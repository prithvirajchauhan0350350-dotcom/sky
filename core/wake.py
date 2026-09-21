"""P6 wake word: openWakeWord 'hey jarvis' (openwakeword 0.4.0 API).

Bundled model path: openwakeword.models["hey_jarvis"]["model_path"]
(hey_jarvis_v0.1.onnx ships inside the wheel). Feed ~1280-sample frames
(~80ms) of int16 PCM to predict(); it returns {model_name: score}.
Callers should reset() after a trigger or a long mute so stale audio
context doesn't linger.
"""
import logging
import re
import time

import numpy as np

log = logging.getLogger("sky.wake")

_model = None


def model():
    """Lazy-load the hey_jarvis wake model (loads in ~0.3s)."""
    global _model
    if _model is None:
        import openwakeword
        from openwakeword.model import Model
        path = openwakeword.models["hey_jarvis"]["model_path"]
        t0 = time.time()
        _model = Model(wakeword_model_paths=[path])
        log.info("wake model loaded in %.1fs (%s)", time.time() - t0, path)
    return _model


def score(m, frame_f32: np.ndarray) -> float:
    """Score one ~1280-sample float32 frame; returns max wake score (0..1)."""
    pcm = (np.clip(frame_f32, -1.0, 1.0) * 32767).astype(np.int16)
    s = m.predict(pcm)
    return max(float(v) for v in s.values()) if s else 0.0


def reset(m):
    try:
        m.reset()
    except Exception:
        pass


# whisper often mangles the wake phrase; accept common spellings.
# Bare "sky" / mangled "shado" also count — the user often drops "hey".
# Hinglish addresses too: "sky ji", "sun sky", "suno sky", plain "suno"/"sun".
_WAKE_RE = re.compile(
    r"^\s*(?:"
    r"(?:hey|hay|hi|hai|he|eh|ae|ay|ok|okay|yo|oye|arre|arey|sun|suno|sunno|aey)?\W{0,3}"
    r"(?:jarvis|jarwis|jervis|jarves|jasvis|jar vis"
    r"|sky|skye|skai|ski|skey|skyi|sky e|shado|shadow"
    r"|skiji|skyji|sky ji)"
    r"(?:\W{0,2}(?:ji|jee))?"
    r"|suno|sunno|sun"
    r")\b[\s,.:;!-]*",
    re.I,
)


def is_wake_text(text: str) -> bool:
    """True if the transcript starts with a wake phrase (hey sky / hey jarvis)."""
    return bool(_WAKE_RE.match(text or ""))


def strip_phrase(text: str) -> str:
    """Remove a spoken 'hey sky/jarvis' prefix left in the transcript."""
    return _WAKE_RE.sub("", text or "", count=1).strip()
