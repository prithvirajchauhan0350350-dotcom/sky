"""A/B beam_size 5 vs 1 on whisper small (int8, CPU, live cpu_threads setting).
Uses scripts/beam_test.npy (7s Hinglish clip). Run: .venv\\Scripts\\python.exe scripts\\test_beam_ab.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np  # noqa: E402
import os  # noqa: E402
from faster_whisper import WhisperModel  # noqa: E402

audio = np.load(Path(__file__).resolve().parent / "beam_test.npy")
t0 = time.time()
m = WhisperModel("small", device="cpu", compute_type="int8",
                 cpu_threads=max(2, os.cpu_count() or 4))
print(f"load {time.time()-t0:.1f}s")

# warm-up pass (first transcribe includes graph warmup)
segs, _ = m.transcribe(audio, beam_size=5, vad_filter=True,
                       condition_on_previous_text=False)
" ".join(s.text for s in segs)

for beam in [5, 1]:
    t1 = time.time()
    segs, _ = m.transcribe(audio, beam_size=beam, vad_filter=True,
                           condition_on_previous_text=False)
    text = " ".join(s.text.strip() for s in segs).strip()
    print(f"beam {beam}: {time.time()-t1:.2f}s | {text!r}")
