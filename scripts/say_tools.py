"""Sky tools intro, spoken in the new emotional voice. Marker file proves it."""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.voice import speak  # noqa: E402

t = ("Haan bhai, suno! Mujhe kaafi saare tools milte hain. "
     "Computer chalana to mera kaam hi hai — shell commands, files padhna, "
     "apps kholna, screen dekhna, mouse aur keyboard bhi mere haath mein hai. "
     "Internet se koi bhi jankari la sakti hoon. "
     "Reminders set, list aur delete — sab yaad rakhti hoon. "
     "System ki sehat bhi check karti hoon — CPU, RAM, battery, diagnostics. "
     "Aur apne deep knowledge se kisi bhi sawal ka gehra jawab. "
     "Spy console se to poora ghar control mein. "
     "To batao, kya karna hai?")

t0 = time.time()
backend = speak(t)
(ROOT / "data" / "_voice_last.txt").write_text(
    f"backend={backend} chars={len(t)} secs={time.time() - t0:.1f} "
    f"at={time.strftime('%H:%M:%S')}\n", encoding="utf-8")
