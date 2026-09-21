"""P6: load bundled hey_jarvis (openwakeword 0.4.0 API), score silence."""
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np  # noqa: E402
import openwakeword  # noqa: E402
from openwakeword.model import Model  # noqa: E402

path = openwakeword.models["hey_jarvis"]["model_path"]
print("model file:", path)
t0 = time.time()
m = Model(wakeword_model_paths=[path])
scores = m.predict(np.zeros((16000,), dtype=np.int16))
pretty = {k: round(float(v), 4) for k, v in scores.items()}
print(f"loaded in {time.time() - t0:.1f}s; silence scores: {pretty}")
