"""Download + load whisper small (int8), transcribe 1s of silence, report timing."""
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np  # noqa: E402

from core import voice  # noqa: E402

t0 = time.time()
txt = voice.transcribe(np.zeros(16000, dtype=np.float32))
print(f"whisper-ready empty-> {txt!r} in {time.time() - t0:.1f}s")
print("mic_available:", voice.mic_available())
try:
    import sounddevice as sd
    ins = [d["name"] for d in sd.query_devices() if d.get("max_input_channels", 0) > 0]
    print("input devices:", ins[:4] or "NONE")
except Exception as e:
    print("device query failed:", e)
