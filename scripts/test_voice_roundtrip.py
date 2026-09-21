"""Voice round-trip: edge-tts speaks a phrase -> wav -> faster-whisper hears it."""
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np  # noqa: E402

from core import voice  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="sky_rt_"))
mp3, wav = tmp / "t.mp3", tmp / "t.wav"
voice._synthesize("Sky reporting for duty, sir.", mp3)
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3),
                "-ar", "16000", "-ac", "1", str(wav)], check=True)
with wave.open(str(wav)) as w:
    n = w.getnframes()
    audio = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768.0
heard = voice.transcribe(audio)
print(f"round-trip: spoken='Sky reporting for duty, sir.' ({n/16000:.1f}s)")
print(f"whisper heard: {heard!r}")
subprocess.run(["rm", "-rf", str(tmp)])

# live mic: 2s capture from the pulse source (silence is fine — just prove it works)
import threading  # noqa: E402
print("mic_available:", voice.mic_available())
stop = threading.Event()
threading.Timer(2.0, stop.set).start()
try:
    audio, secs = voice.record_until(stop)
    print(f"mic capture ok: {0 if audio is None else len(audio)} samples in 2s window")
except Exception as e:
    print("mic capture FAILED:", e)
