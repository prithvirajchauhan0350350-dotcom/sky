"""Voice: ears (faster-whisper, local CPU) + mouth (edge-tts -> mp3 -> speakers).

Windows-native: playback via the Windows MCI (winmm) API — no ffmpeg needed
(same proven path Spy used). STT fails soft: callers must catch
VoiceUnavailable and degrade to text mode.
"""
import asyncio
import ctypes
import logging
import os
import re
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger("sky.voice")

_HOLO_URL = "http://127.0.0.1:20129/say"  # hologram_app.py control port


def _holo_say_async(text: str):
    """Fire-and-forget: hand the line to hologram_app.py for lip sync.
    Silently no-ops when the hologram app isn't running."""
    try:
        req = urllib.request.Request(
            _HOLO_URL,
            data=urllib.parse.urlencode({"text": text[:1000]}).encode(),
            method="POST")
        urllib.request.urlopen(req, timeout=1).close()
    except Exception:
        pass


def _holo_up() -> bool:
    """True when hologram_app.py is actually running on its control port."""
    try:
        urllib.request.urlopen("http://127.0.0.1:20129/ping", timeout=0.3).close()
        return True
    except Exception:
        return False


def _holo_enabled() -> bool:
    try:
        import json
        cfg = json.loads((Path(__file__).resolve().parent.parent
                          / "config.json").read_text())
        return bool(cfg.get("hologram"))
    except Exception:
        return False

VOICE_EN = "en-IN-NeerjaExpressiveNeural"  # Indian English, FEMALE, expressive
VOICE_HI = "hi-IN-SwaraNeural"     # Hindi, FEMALE — closest TTS has to Haryanvi
TTS_RATE = os.environ.get("SKY_TTS_RATE", "+5%")  # flat-path speaking rate
_SR = 16000


# ---------------- speech text hygiene ----------------
# LLM output is written for reading, not speaking. Markdown, emoji, urls and
# code tokens either get read aloud ("star star RAM") or wreck the prosody
# (TTS pauses on every symbol). Everything spoken goes through _clean_speech.

_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF"
    "\U0000FE00-\U0000FE0F\U0000200D\U00002B00-\U00002BFF]")

_MARKDOWN_RE = re.compile(r"(\*\*|\*|__|~~|`|#+\s*|^\s*[-•>]\s+|\|)", re.M)


def _clean_speech(text: str) -> str:
    """Strip everything TTS shouldn't say; smooth the punctuation joints."""
    t = text or ""
    t = re.sub(r"```.*?```", " ", t, flags=re.S)   # fenced code blocks
    t = re.sub(r"https?://\S+", " link ", t)        # urls -> "link"
    t = _MARKDOWN_RE.sub(" ", t)                    # md markers, bullets, pipes
    t = _EMOJI_RE.sub(" ", t)                       # emoji / dingbats
    t = re.sub(r"\b(\w+)_(\w+)\b", r"\1 \2", t)     # snake_case -> words
    t = t.replace("\u2014", ", ").replace("\u2013", ", ")  # dashes -> breath
    t = t.replace("...", ", ").replace(". . .", ", ")
    t = re.sub(r"!{2,}", "!", t)
    t = re.sub(r"\?{2,}", "?", t)
    t = re.sub(r"\.{2,}", ".", t)
    t = re.sub(r"\(\s*\)|\[\s*\]", " ", t)          # empty parens/brackets
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t


def clean_for_speech(text: str) -> str:
    """Public alias — UI cleans a copy for TTS, keeps raw text on screen."""
    return _clean_speech(text)


def _truncate_speech(text: str, limit: int = 900) -> str:
    """Cap length at a sentence boundary — never stop mid-word."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    last_end = 0
    for m in re.finditer(r"[.!?।]", cut):
        last_end = m.end()
    if last_end and last_end >= limit * 0.5:
        return cut[:last_end].strip()
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > limit * 0.5 else cut).strip()


class VoiceUnavailable(Exception):
    pass

# romanized Hindi tokens. STRONG: one hit => Hindi voice. WEAK: need >= 2.
_HI_STRONG = re.compile(
    r"\b(kya|kaise|kaisa|kaisi|nahi|nahin|matlab|acha|achha|theek|thik"
    r"|karo|karna|kardo|mujhe|mera|meri|aap|tum|yeh|woh|kahan|kyun|kyu"
    r"|bhai|toh|haan|zara|abhi|chalu|bandh|chahiye|dedo|dijiye|batao"
    r"|bhejo|dikhao|kholo|chalao|shukriya|dhanyavad|hain)\b",
    re.I,
)
_HI_WEAK = re.compile(
    r"\b(hai|kar|aur|bhi|ji|kal|aaj|kab|wo|ye|pani|paani|sun|suno)\b",
    re.I,
)


def _is_hinglish(text: str) -> bool:
    """Romanized Hindi (Hinglish in Latin script) detection."""
    t = text or ""
    return bool(_HI_STRONG.findall(t)) or len(_HI_WEAK.findall(t)) >= 2


def pick_voice(text: str) -> str:
    """Voice by language: Devanagari or Hinglish -> Hindi voice, else Indian English."""
    if _has_devanagari(text) or _is_hinglish(text):
        return os.environ.get("SKY_TTS_VOICE_HI", VOICE_HI)
    return os.environ.get("SKY_TTS_VOICE", VOICE_EN)


# ---------------- mouth ----------------

def _has_devanagari(text: str) -> bool:
    return len(re.findall(r"[ऀ-ॿ]", text)) > len(text) * 0.15


def _synthesize(text: str, out_mp3: Path, voice: str | None = None,
                rate: str | None = None, pitch: str | None = None):
    import edge_tts
    t = _truncate_speech(_clean_speech(text))
    v = voice or pick_voice(t)
    r = rate or TTS_RATE
    if pitch:
        asyncio.run(edge_tts.Communicate(t[:1000], v, rate=r,
                                         pitch=pitch).save(str(out_mp3)))
    else:
        asyncio.run(edge_tts.Communicate(t[:1000], v, rate=r).save(str(out_mp3)))


_mci_counter = 0


def _play_mp3_windows(path: str):
    """Play an mp3: miniaudio decode -> sounddevice PCM (clean, bit-accurate).

    MCI mpegvideo (the old fallback) decodes 24kHz mono mp3 with a legacy
    codec that sounds robotic/crackly on many machines, so it is now the
    fallback, not the primary path."""
    try:
        import numpy as np
        import miniaudio
        import sounddevice as sd
        decoded = miniaudio.decode_file(
            str(path), nchannels=2, sample_rate=48000,
            output_format=miniaudio.SampleFormat.SIGNED16)
        data = np.asarray(decoded.samples, dtype=np.int16).reshape(-1, 2)
        sd.play(data, decoded.sample_rate)
        sd.wait()
        return
    except Exception as e:
        log.warning("pcm playback failed (%s) — falling back to MCI", e)
    global _mci_counter
    _mci_counter += 1
    alias = f"sky_tts_{_mci_counter}"
    vpath = str(path).replace("/", "\\")
    winmm = ctypes.windll.winmm
    winmm.mciSendStringW(f'open "{vpath}" type mpegvideo alias {alias}', None, 0, None)
    winmm.mciSendStringW(f"play {alias} wait", None, 0, None)
    winmm.mciSendStringW(f"close {alias}", None, 0, None)


# ---------------- emotional mouth ----------------
# Per-sentence mood (rate, pitch): speech varies like a real person instead
# of one flat robotic delivery.
_MOOD_WARM = ("+5%", "+1Hz")      # default
_MOOD_BRIGHT = ("+12%", "+4Hz")   # exclamations, good news
_MOOD_RISING = ("+7%", "+3Hz")    # questions
_MOOD_SERIOUS = ("+2%", "-3Hz")   # warnings, errors

_BRIGHT_RE = re.compile(
    r"[!]|\b(badhiya|badhi|shabash|kamaal|wow|great|excellent|zabardast"
    r"|badi news|sahi hai|perfect|ho gaya)\b", re.I)
_RISING_RE = re.compile(
    r"\?|\b(kya|kaise|kaisa|kaisi|kab|kahan|kaun|kitna|kitni|kyun|kyu"
    r"|batayiye|batao)\b", re.I)
_SERIOUS_RE = re.compile(
    r"\b(dhyan|warning|khatra|serious|problem|error|galat|hoshiyar"
    r"|alert|urgent)\b", re.I)


def _split_sentences(text: str) -> list:
    parts = re.split(r"(?<=[.!?।])\s+|\n+", text.strip())
    out, buf = [], ""
    for p in parts:
        p = p.strip()
        if not p:
            continue
        buf = (buf + " " + p).strip()
        if len(buf) >= 12:  # merge tiny fragments into one segment
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return out


def _pick_mood(sentence: str) -> tuple:
    if _SERIOUS_RE.search(sentence):
        return _MOOD_SERIOUS
    if _BRIGHT_RE.search(sentence):
        return _MOOD_BRIGHT
    if _RISING_RE.search(sentence):
        return _MOOD_RISING
    return _MOOD_WARM


async def _synth_seg(text: str, out_mp3: Path, voice: str,
                     rate: str, pitch: str):
    import edge_tts
    await edge_tts.Communicate(text, voice, rate=rate, pitch=pitch).save(
        str(out_mp3))


def _decode_mp3(path: Path):
    import miniaudio
    import numpy as np
    d = miniaudio.decode_file(str(path), nchannels=2, sample_rate=48000,
                              output_format=miniaudio.SampleFormat.SIGNED16)
    return np.asarray(d.samples, dtype=np.int16).reshape(-1, 2)


def _speak_emotional(text: str, tmp: Path) -> None:
    import numpy as np
    import sounddevice as sd
    voice = pick_voice(text)
    text = _truncate_speech(_clean_speech(text))
    sents = _split_sentences(text) or [text]
    if len(sents) > 12:  # cap: join the tail into one segment
        sents = sents[:11] + [" ".join(sents[11:])]
    token = f"{int(time.time() * 1000)}"

    async def _all():
        await asyncio.gather(*(
            _synth_seg(s, tmp / f"seg_{token}_{i}.mp3", voice,
                       *_pick_mood(s))
            for i, s in enumerate(sents)))

    asyncio.run(_all())
    try:
        parts = [_decode_mp3(tmp / f"seg_{token}_{i}.mp3")
                 for i in range(len(sents))]
        if len(parts) == 1:
            joined = parts[0]
        else:
            gap = np.zeros((int(48000 * 0.3), 2), dtype=np.int16)
            joined = np.concatenate(
                [p for tup in zip(parts, [gap] * len(parts)) for p in tup])
        sd.play(joined, 48000)
        sd.wait()
    finally:
        for i in range(len(sents)):
            try:
                (tmp / f"seg_{token}_{i}.mp3").unlink(missing_ok=True)
            except Exception:
                pass


def speak(text: str) -> str:
    """Speak aloud: emotional per-sentence prosody, clean PCM playback."""
    text = _clean_speech(text)
    if _holo_enabled() and _holo_up():
        # hologram owns the audio (it synthesizes with word timings for
        # lip sync); we just hand the line over and stay quiet here.
        threading.Thread(target=_holo_say_async, args=(text,), daemon=True).start()
        log.info("spoke via hologram: %d chars", len(text))
        return "hologram"
    tmp = Path(tempfile.gettempdir()) / "sky_tts"
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        _speak_emotional(text, tmp)
        log.info("spoke %d chars via pcm-emotional", len(text))
        return "pcm"
    except Exception as e:
        log.warning("emotional speak failed (%s) — flat fallback", e)
    mp3 = tmp / f"reply_{int(time.time() * 1000)}.mp3"
    try:
        _synthesize(text, mp3)
        _play_mp3_windows(mp3)
        log.info("spoke %d chars via mci", len(text))
        return "mci"
    finally:
        try:
            mp3.unlink(missing_ok=True)
        except Exception:
            pass


# ---------------- ears ----------------

def mic_available() -> bool:
    try:
        import sounddevice as sd
        return any(d.get("max_input_channels", 0) > 0 for d in sd.query_devices())
    except Exception as e:
        log.warning("sounddevice query failed: %s", e)
        return False


def record_until(stop_event: threading.Event, sr: int = 16000):
    """Record mic until stop_event is set. Returns (numpy float32 mono, seconds)."""
    import numpy as np
    import sounddevice as sd
    if not mic_available():
        raise VoiceUnavailable("no microphone visible to Windows")
    chunks = []

    def cb(indata, frames, t, status):
        chunks.append(indata.copy())

    with sd.InputStream(samplerate=sr, channels=1, dtype="float32", callback=cb):
        while not stop_event.is_set():
            time.sleep(0.05)
    if not chunks:
        return None, 0.0
    audio = b"".join(c.tobytes() for c in chunks)
    arr = np.frombuffer(audio, dtype=np.float32)
    return arr, len(arr) / sr


_whisper = None

# domain conditioning: keeps tech vocab (Sky/CPU/RAM...) in Latin script,
# improves recognition of Hinglish + computer words
_PROMPT = ("Hinglish tech talk: Sky, CPU, RAM, disk, terminal, WiFi, battery, "
           "reminder, briefing, OmniRoute. Kya bolu? Theek hai bhai.")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).strip()


def _strip_prompt_echo(said: str) -> str:
    """Whisper parrots the initial_prompt on short/quiet audio (e.g.
    'OmniRoute. Kya bolu? Theek hai bhai.') — drop those echoes."""
    s = _norm(said)
    if not s or len(s) > 80:
        return said
    if s in _norm(_PROMPT):
        return ""
    return said


def transcribe(audio) -> str:
    """faster-whisper transcription of float32 mono audio."""
    global _whisper
    import numpy as np
    if audio is None or len(audio) < 8000:  # <0.5s
        return ""
    if _whisper is None:
        from faster_whisper import WhisperModel
        size = os.environ.get("SKY_WHISPER_MODEL", "small")
        log.info("loading whisper %s (int8, cpu)", size)
        _whisper = WhisperModel(size, device="cpu", compute_type="int8",
                                cpu_threads=max(2, os.cpu_count() or 4))
    segments, info = _whisper.transcribe(
        audio, beam_size=5, vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=350, speech_pad_ms=60,
                            threshold=0.35),
        condition_on_previous_text=False,
        initial_prompt=os.environ.get("SKY_WHISPER_PROMPT", _PROMPT))
    said = " ".join(s.text.strip() for s in segments).strip()
    return _strip_prompt_echo(said)
