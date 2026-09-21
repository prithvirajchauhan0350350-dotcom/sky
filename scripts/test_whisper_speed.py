"""Measure whisper small vs medium (int8, CPU): load time + transcribe latency
on a Hinglish clip. Decides if SKY can afford 'medium' for perfect vocab."""
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np  # noqa: E402
from core import voice  # noqa: E402

TEXT = "Hey Sky, CPU tez chal raha hai. RAM kitni lagi hai batao jaldi."
tmp = Path(tempfile.mkdtemp(prefix="sky_wspeed_"))
mp3, wav = tmp / "t.mp3", tmp / "t.wav"
voice._synthesize(TEXT, mp3)
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3),
                "-ar", "16000", "-ac", "1", str(wav)], check=True)
raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(wav),
                      "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
                     capture_output=True, check=True).stdout
audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

import psutil  # noqa: E402
from faster_whisper import WhisperModel  # noqa: E402

for size in ["small", "medium"]:
    t0 = time.time()
    m = WhisperModel(size, device="cpu", compute_type="int8")
    load = time.time() - t0
    t1 = time.time()
    segs, _ = m.transcribe(audio, beam_size=5, vad_filter=True,
                           condition_on_previous_text=False)
    text = " ".join(s.text.strip() for s in segs).strip()
    dt = time.time() - t1
    print(f"{size:6s}: load {load:5.1f}s | transcribe {dt:5.1f}s | "
          f"ram {psutil.Process().memory_info().rss/1e9:.2f} GB | heard: {text!r}")
    del m

subprocess.run(["rm", "-rf", str(tmp)])
