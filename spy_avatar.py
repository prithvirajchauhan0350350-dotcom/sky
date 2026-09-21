"""
spy_avatar.py — Spy's Avatar & Animation Engine (real lip sync).
----------------------------------------------------------------
Converts Spy's spoken audio into mouth movements (visemes), the way
realtime avatar stacks do:

  1. SYNTHESIS WITH TIMING — edge-tts streams the MP3 and, alongside the
     audio, emits WordBoundary events: the exact start time and duration
     of every word it speaks. That gives a true audio timeline.
  2. VISEME MAPPING — each word's letters are expanded into viseme
     (mouth-shape) keyframes: vowels control openness/width, consonants
     close or narrow the mouth (M/B/P close it, E/I widen it, O/U round
     it...). Letters are distributed across the word's REAL duration.
  3. ANIMATED PLAYBACK — the MP3 plays through Windows MCI while the
     playback position is polled ~30x/second; the viseme at the current
     position is pushed to the hologram via a callback.

The mouth therefore moves because of when the voice actually speaks each
word — not a fake sine wave. If word timings are unavailable the caller
falls back to its old behaviour gracefully.

Facial expressions are handled by spy_hologram (happy / alert / neutral);
this module only produces the mouth channel.

Runs on the TTS thread; all GUI updates happen through the callback.
"""

import asyncio
import os
import time

try:
    import edge_tts
except ImportError:
    edge_tts = None

PLAYBACK_POLL_SECONDS = 0.033  # ~30 viseme updates per second

# ---------------------------------------------------------------------------
# Visemes: letter → (openness 0..1, width 0..1)
# ---------------------------------------------------------------------------

def _letter_viseme(ch: str):
    """A compact Preston-Blair-style mapping. Openness lifts the jaw,
    width stretches the mouth corners."""
    c = ch.lower()
    if c in "aàáâãä":   return (0.85, 0.55)   # open jaw
    if c in "eèéêë":    return (0.55, 0.95)   # wide
    if c in "iìíîï":    return (0.32, 1.00)   # widest, less open
    if c in "oòóôõö":   return (0.70, 0.38)   # rounded open
    if c in "uùúûü":    return (0.42, 0.28)   # puckered
    if c in "mbp":      return (0.00, 0.50)   # lips pressed together
    if c in "fv":       return (0.18, 0.62)   # teeth on lip
    if c == "w":        return (0.35, 0.25)   # tight round
    if c in "szxc":     return (0.24, 0.72)   # slit
    if c in "tdnlr":    return (0.35, 0.60)   # tongue behind teeth
    if c in "kgjqy":    return (0.50, 0.55)   # back of throat, mid open
    if c in "h":        return (0.45, 0.50)
    if c in "j":        return (0.55, 0.60)
    return (0.40, 0.55)                         # anything else


# ---------------------------------------------------------------------------
# 1. Synthesis with word timings
# ---------------------------------------------------------------------------

def synthesize_with_timing(text: str, voice: str, out_path: str,
                           rate: str | None = None):
    """Speak `text` with edge-tts; write the MP3 to out_path and return
    (path, words) where words = [(start_s, duration_s, word), ...].
    `rate` adjusts speaking speed in edge-tts format (e.g. "+15%" for a
    little faster); word timings stay aligned to the actual audio, so
    lip sync is unaffected. Returns (None, []) on failure — caller
    falls back."""
    if edge_tts is None:
        return None, []
    words = []

    async def _gen():
        # boundary='WordBoundary': stream word start/duration events
        # (older edge-tts did this by default; 7.x needs it requested)
        kwargs = {"rate": rate} if rate else {}
        try:
            comms = edge_tts.Communicate(text, voice, boundary="WordBoundary",
                                         **kwargs)
        except TypeError:
            try:
                comms = edge_tts.Communicate(text, voice, **kwargs)
            except TypeError:          # ancient edge-tts: no rate support
                comms = edge_tts.Communicate(text, voice)
        with open(out_path, "wb") as fh:
            async for chunk in comms.stream():
                if chunk["type"] == "audio":
                    fh.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    # offsets/durations are in 100-nanosecond ticks
                    words.append((chunk["offset"] / 1e7,
                                  chunk["duration"] / 1e7,
                                  chunk["text"]))

    try:
        asyncio.run(_gen())
    except Exception:
        return None, []
    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        return None, []
    return out_path, words


# ---------------------------------------------------------------------------
# 2. Viseme timeline
# ---------------------------------------------------------------------------

def build_viseme_timeline(words, step_s: float = PLAYBACK_POLL_SECONDS):
    """Expand word timings into keyframes [(t_s, openness, width), ...] at
    a fixed step. Each word's letters map to visemes across its real
    duration, with a brief lip-close at the end of every word."""
    if not words:
        return []
    total = words[-1][0] + words[-1][1] + 0.35   # little rest at the end
    n_frames = int(total / step_s) + 1
    frames = []
    wi = 0
    for i in range(n_frames):
        t = i * step_s
        while wi + 1 < len(words) and t >= words[wi][0] + words[wi][1]:
            wi += 1
        start, dur, word = words[wi]
        if t < start:
            op, wd = 0.04, 0.50                  # waiting to speak: closed
        else:
            letters = [c for c in word if c.isalpha()]
            seq = [_letter_viseme(c) for c in letters] or [(0.4, 0.55)]
            frac = (t - start) / max(dur, 1e-4)
            if frac >= 0.88:
                op, wd = 0.05, 0.50              # word-end lip close
            else:
                idx = min(len(seq) - 1, int(frac / 0.88 * len(seq)))
                op, wd = seq[idx]
        frames.append((t, op, wd))
    return frames


# ---------------------------------------------------------------------------
# 3. Animated playback (MCI position polling → callback)
# ---------------------------------------------------------------------------

_avi_counter = 0


def play_animated(mp3_path: str, frames, mouth_cb, poll_s: float = PLAYBACK_POLL_SECONDS) -> bool:
    """Play the MP3 and while it plays, call mouth_cb(openness, width)
    ~30x/second at the REAL playback position. Returns False (having
    played nothing) if MCI can't open the file or frames are empty."""
    if not frames:
        return False
    import ctypes
    global _avi_counter
    winmm = ctypes.windll.winmm
    _avi_counter += 1
    alias = f"spy_ava_{_avi_counter}"
    vpath = str(mp3_path).replace("/", "\\")
    buf = ctypes.create_unicode_buffer(128)

    if winmm.mciSendStringW(f'open "{vpath}" type mpegvideo alias {alias}',
                            None, 0, None):
        return False
    winmm.mciSendStringW(f"status {alias} length", buf, 128, None)
    try:
        length_ms = int(buf.value)
    except ValueError:
        length_ms = 0

    winmm.mciSendStringW(f"play {alias}", None, 0, None)
    fi = 0
    try:
        while True:
            winmm.mciSendStringW(f"status {alias} position", buf, 128, None)
            try:
                pos_s = int(buf.value) / 1000.0
            except ValueError:
                break
            if length_ms and pos_s * 1000 >= length_ms:
                break
            while fi + 1 < len(frames) and frames[fi + 1][0] < pos_s:
                fi += 1
            _t, op, wd = frames[fi]
            try:
                mouth_cb(op, wd)
            except Exception:
                pass
            time.sleep(poll_s)
        # let the tail of the audio finish before closing
        winmm.mciSendStringW(f"play {alias} wait", None, 0, None)
    finally:
        winmm.mciSendStringW(f"close {alias}", None, 0, None)
    return True


if __name__ == "__main__":
    # Self-test: synthesize, print the real word timings, build the
    # viseme track and show keyframes (no audio played).
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    demo = "Good evening, sir. All systems are online."
    path, wds = synthesize_with_timing(demo, "hi-IN-SwaraNeural",
                                       "_spy_avatar_test.mp3")
    print("mp3:", path and os.path.getsize(path), "bytes | words:", len(wds))
    for w in wds[:6]:
        print(f"  {w[0]:6.2f}s +{w[1]:5.2f}s  {w[2]!r}")
    frames = build_viseme_timeline(wds)
    print("frames:", len(frames))
    for k in range(0, len(frames), max(1, len(frames) // 10)):
        t, op, wd = frames[k]
        print(f"  t={t:5.2f}s  open={op:.2f}  wide={wd:.2f}")
    if path and os.path.exists(path):
        os.remove(path)
    print("OK" if wds and frames else "NO TIMINGS (fallback would be used)")
