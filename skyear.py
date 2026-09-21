#!/usr/bin/env python3
"""SKY background ear: always-on 'hey sky' listener. Run: .venv/bin/python skyear.py

- openWakeWord catches 'hey sky' (jarvis-pattern model) instantly (scores every 80ms frame)
- VAD + whisper catches 'hey sky' (and variants) at utterance end
- after each reply a 20s open-mic window: keep talking, no wake phrase needed
- risky shell commands are confirmed ALOUD (say yes / no)
- self-heals: mic reopens on failure; whisper preloaded at start
Log: logs/sky.log (tag skyear)
"""
import logging
import re
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import collections

import numpy as np

from sky import agent_turn, load_cfg, setup_logging
from core import persona, voice, wake
from core.memory import Memory

log = logging.getLogger("skyear")

FLOOR_MIN = 0.004
TRAIL_SILENCE = 0.9
MAX_UTT = 12.0
OPEN_MIC_S = 20.0

_YES_RE = re.compile(r"\b(yes|yeah|yep|haan|han|ha|kar do|kardo|ok|okay|go|confirm)\b", re.I)
_NO_RE = re.compile(r"\b(no|nope|nahi|nahin|mat|cancel|ruk|ruko|stop)\b", re.I)


def _yes_no(text):
    if _YES_RE.search(text or ""):
        return "y"
    if _NO_RE.search(text or ""):
        return "n"
    return None


# whisper hallucinates on ambient noise ("Thank you.", "ko", "Bye.") —
# open-mic must not turn these into phantom commands.
_JUNK_RE = re.compile(
    r"^(?:thank(?:s| you)?(?: for watching)?|bye(?:-bye)?|okay?|hm+|um+|uh+|"
    r"m-hmm+|what|so|the|music|please|yeah|hello(?: there)?|subscribe)\b[\s.,!?-]*$",
    re.I,
)


def _is_junk(text, voiced_s):
    """True = ambient-noise artifact, not a real command."""
    t = (text or "").strip()
    if len(t) < 4:
        return True
    if _JUNK_RE.match(t):
        return True
    # short transcript with little real speech behind it -> noise
    return voiced_s < 0.5 and len(t.split()) < 3


def speak(text):
    print("sky>", text, flush=True)
    try:
        voice.speak(text)
    except Exception as e:
        log.info("speech unavailable (%s)", str(e)[:80])


class Ear:
    def __init__(self):
        self.pending = collections.deque()
        self.floor = FLOOR_MIN

    def _cb(self, indata, frames, t, status):
        self.pending.append(indata.copy())

    def open(self):
        import sounddevice as sd
        return sd.InputStream(samplerate=16000, channels=1, dtype="float32",
                              blocksize=1280, callback=self._cb)

    def next_frame(self, timeout=1.0):
        """Pop one >=1280-sample frame; returns (frame, rms) or None."""
        t0 = time.time()
        acc = np.empty((0,), dtype=np.float32)
        while len(acc) < 1280:
            if self.pending:
                acc = np.concatenate([acc, self.pending.popleft().flatten()])
            elif time.time() - t0 > timeout:
                return None
            else:
                time.sleep(0.01)
        frame, acc = acc[:1280], acc[1280:]
        if len(acc):
            self.pending.appendleft(acc.reshape(-1, 1))
        rms = float(np.sqrt(np.mean(frame * frame)))
        self.floor = 0.99 * self.floor + 0.01 * rms
        return frame, rms


def capture_utterance(ear, max_s=MAX_UTT):
    """Collect frames until 1.2s of quiet after speech (or max_s). -> (audio|None)."""
    ear.pending.clear()  # drop stale audio queued while we were thinking/speaking
    buf, spoke, last_voice = [], False, 0.0
    while True:
        got = ear.next_frame()
        if got is None:
            break
        frame, rms = got
        if rms > max(FLOOR_MIN, 3.5 * ear.floor):
            if not spoke:
                spoke = True
                log.info("speech started")
            last_voice = time.time()
        buf.append(frame)
        dur = len(buf) * 1280 / 16000
        if spoke and time.time() - last_voice > TRAIL_SILENCE:
            break
        if dur >= max_s:
            break
        if not spoke and dur >= 2.5:
            return None  # noise blip, not speech
    return np.concatenate(buf) if buf else None


def handle_command(cfg, mem, ear, text):
    """One agent turn; risky shell asked aloud."""
    def gate(cmd):
        speak(f"क्या मैं ये चला दूँ: {cmd[:60]}? हाँ या ना कहिए।")
        audio = capture_utterance(ear, max_s=5.0)
        ans = voice.transcribe(audio) if audio is not None else ""
        log.info("confirm heard: %r", ans)
        ok = _yes_no(ans) == "y"
        if not ok:
            speak("रोक दिया, सर।")
        return ok
    reply = agent_turn(cfg, mem, text[:cfg.get("input_char_cap", 4000)], gate)
    say_text, _show = persona.split_bilingual(reply)
    speak(say_text)


def main():
    from core import single_instance
    if not single_instance("Local\\SKY_EAR_SINGLETON"):
        log.info("skyear already running — exiting quietly")
        return
    setup_logging()
    cfg = load_cfg()
    mem = Memory(ROOT / "data" / "memory.db")

    if not voice.mic_available():
        log.error("no microphone visible to WSL — ear cannot start")
        sys.exit(1)

    # warm the ears + mouth so the first wake answers fast
    log.info("warming whisper...")
    voice.transcribe(np.zeros(16000, dtype=np.float32))
    try:
        oww = wake.model()
    except Exception as e:
        oww = None
        log.warning("openWakeWord unavailable (%s) — whisper spotting only", e)

    ear = Ear()
    global OPEN_MIC_S
    OPEN_MIC_S = float(cfg.get("open_mic_s", 45))
    log.info("SKY EAR online: say 'sky' (or 'sun sky', 'sky ji')")
    speak("स्काई अब हमेशा सुन रही है सर, बस बोलिए: स्काई।")

    state = {"mode": "idle", "open_until": 0.0}
    rec, acc = [], np.empty((0,), dtype=np.float32)

    while True:
        try:
            with ear.open() as stream:
                while True:
                    got = ear.next_frame(timeout=3600)
                    if got is None:
                        continue
                    frame, rms = got

                    if state["mode"] == "busy":
                        if len(ear.pending) > 125:  # ~2s: keep deque bounded
                            ear.pending.clear()
                        continue  # mic muted while thinking/speaking

                    if state["mode"] == "rec":
                        rec.append(frame)
                        if rms > max(FLOOR_MIN, 3.5 * ear.floor):
                            state["last_voice"] = time.time()
                            state["voiced"] += 1280 / 16000
                        dur = len(rec) * 1280 / 16000
                        if (time.time() - state["last_voice"] > TRAIL_SILENCE) or dur >= MAX_UTT:
                            audio = np.concatenate(rec)
                            rec.clear()
                            state["mode"] = "busy"
                            said = ""
                            try:
                                said = voice.transcribe(audio)
                            except Exception as e:
                                log.warning("transcribe failed: %s", e)
                            log.info("heard %.1fs: %r", dur, said)
                            open_mic = time.time() < state["open_until"]
                            if wake.is_wake_text(said):
                                cmd = wake.strip_phrase(said)
                            elif open_mic and said and not _is_junk(said, state["voiced"]):
                                cmd = said
                            else:
                                cmd = ""  # background chatter — ignore
                            if cmd:
                                handle_command(cfg, mem, ear, cmd)
                                state["open_until"] = time.time() + OPEN_MIC_S
                            elif wake.is_wake_text(said):
                                speak("जी, सर?")
                                cmd_audio = capture_utterance(ear, max_s=8.0)
                                cmd2 = voice.transcribe(cmd_audio) if cmd_audio is not None else ""
                                if cmd2:
                                    handle_command(cfg, mem, ear, cmd2)
                                    state["open_until"] = time.time() + OPEN_MIC_S
                            ear.pending.clear()
                            if oww is not None:
                                wake.reset(oww)
                            time.sleep(0.5)  # echo cooldown
                            state["mode"] = "idle"
                        continue

                    # --- idle mode ---
                    if oww is not None:
                        s = wake.score(oww, frame)
                        if s >= float(cfg.get("wake_threshold", 0.5)):
                            log.info("OWW wake (score %.2f)", s)
                            wake.reset(oww)
                            speak("जी, सर?")
                            state["mode"] = "busy"
                            cmd_audio = capture_utterance(ear)
                            cmd = voice.transcribe(cmd_audio) if cmd_audio is not None else ""
                            log.info("command after OWW: %r", cmd)
                            if cmd:
                                handle_command(cfg, mem, ear, cmd)
                            state["open_until"] = time.time() + OPEN_MIC_S
                            ear.pending.clear()
                            wake.reset(oww)
                            time.sleep(0.5)
                            state["mode"] = "idle"
                            continue
                        # not a wake hit — fall through to VAD so "hey sky" still works

                    # VAD: start of possible speech ("hey sky" lands here)
                    if rms > max(FLOOR_MIN, 3.5 * ear.floor):
                        state["mode"] = "rec"
                        rec.clear()
                        rec.append(frame)
                        state["last_voice"] = time.time()
                        state["voiced"] = 0.0
        except KeyboardInterrupt:
            log.info("ear stopped by user")
            return
        except Exception as e:
            log.exception("ear loop error: %s — reopening mic in 5s", e)
            time.sleep(5.0)


if __name__ == "__main__":
    main()
