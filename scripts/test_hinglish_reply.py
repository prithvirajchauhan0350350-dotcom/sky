"""End-to-end: Hinglish question -> real brain reply -> which voice speaks it."""
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import voice  # noqa: E402

import os
ROOT = Path(__file__).resolve().parent.parent
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe") if os.name == "nt" \
    else os.path.join(ROOT, ".venv", "bin", "python")
r = subprocess.run(
    [PY, "sky.py", "--ask", "aaj weather kaisa hai bhai?"],
    capture_output=True, text=True, timeout=180, cwd=str(ROOT))
reply = (r.stdout or "").strip() or r.stderr.strip()
print("brain reply:", reply)
print("spoken with :", voice.pick_voice(reply))
print("RESULT:", "PASS" if voice._is_hinglish(reply) else "FAIL")
