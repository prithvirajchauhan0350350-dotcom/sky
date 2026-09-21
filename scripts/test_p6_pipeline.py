"""P6 end-to-end OFFLINE chain test (no mic needed):
edge-tts clip 'Hey Jarvis, ...' -> wake model triggers -> whisper transcribes
-> wake-phrase stripped. Proves the pipeline listen_mode() uses.
"""
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np  # noqa: E402

from core import voice, wake  # noqa: E402

TEXT = "Hey Jarvis. What is the CPU usage right now?"
tmp = Path(tempfile.mkdtemp(prefix="sky_p6_"))
mp3, wav = tmp / "c.mp3", tmp / "c.wav"

print("1) synthesizing:", TEXT)
voice._synthesize(TEXT, mp3)
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3),
                "-ar", "16000", "-ac", "1", str(wav)], check=True)

raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(wav),
                      "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
                     capture_output=True, check=True).stdout
pcm_i16 = np.frombuffer(raw, dtype=np.int16)
audio_f32 = pcm_i16.astype(np.float32) / 32768.0
print(f"   clip: {len(audio_f32)/16000:.1f}s")

print("2) feeding 1280-sample frames to wake model...")
m = wake.model()
best, trig_at = 0.0, None
for i in range(0, len(audio_f32) - 1279, 1280):
    s = wake.score(m, audio_f32[i:i + 1280])
    best = max(best, s)
    if s >= 0.5 and trig_at is None:
        trig_at = (i / 16000, s)
wake.reset(m)
print(f"   max score={best:.3f}  trigger={'YES at %.1fs (score %.2f)' % trig_at if trig_at else 'NO'}")

print("3) whisper transcribe (loads ~4s first time)...")
t0 = time.time()
said = voice.transcribe(audio_f32)
print(f"   heard ({time.time()-t0:.1f}s): {said!r}")

print("4) strip wake phrase ->", repr(wake.strip_phrase(said)))

ok = trig_at is not None and "cpu" in said.lower()
print("RESULT:", "PASS" if ok else "PARTIAL/FAIL (see scores above)")
subprocess.run(["rm", "-rf", str(tmp)])
