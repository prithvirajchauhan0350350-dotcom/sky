"""Long (~20s) Neerja sample: same clean PCM playback path."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.voice import _play_mp3_windows, _synthesize  # noqa: E402

TMP = Path(__file__).resolve().parent.parent / "_voice_ab"
TMP.mkdir(exist_ok=True)

LINE = ("Namaste bhai, main Sky hoon. Ye Neerja ki awaaz ka lamba test hai. "
        "Dekho, jab main bolti hoon to har shabd saaf aana chahiye — chahe "
        "Hindi ho, English ho, ya dono ka mixture. Aaj tumhare PC mein RAM "
        "ninety-four percent bhari thi, isliye sab kuch thoda slow chal raha "
        "tha. Ab main halki aur tez ho gayi hoon. Terminal, browser, "
        "reminders, morning briefing — sab kaam main jhat se kar deti hoon. "
        "Batao, is awaaz mein kuch aur check karna hai, ya final kar dein?")

mp3 = TMP / "long_C_en-IN-NeerjaNeural.mp3"
if not mp3.exists() or mp3.stat().st_size == 0:
    _synthesize(LINE, mp3, "en-IN-NeerjaNeural")
print(f"size {mp3.stat().st_size}B (~{mp3.stat().st_size * 8 / 48000:.0f}s)",
      flush=True)
_play_mp3_windows(str(mp3))
print("done", flush=True)
