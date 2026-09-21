"""A/B/C voice test: same Hinglish line, 3 voices, clean PCM playback.

Run: .venv\\Scripts\\python.exe scripts\\voice_ab.py
Keeps the mp3s in %TEMP%\\sky_tts so you can replay any of them.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.voice import _play_mp3_windows, _synthesize  # noqa: E402

TMP = Path(__file__).resolve().parent.parent / "_voice_ab"
TMP.mkdir(exist_ok=True)

LINE = ("Namaste bhai, main Sky hoon. Aaj ka test — RAM, CPU, WiFi, "
        "sab theek chal rahe hain. Ye awaaz ab clear lag rahi hai?")

VOICES = [
    ("A", "hi-IN-SwaraNeural", "Swara (abhi wali)"),
    ("B", "en-US-AvaMultilingualNeural", "Ava multilingual"),
    ("C", "en-IN-NeerjaNeural", "Neerja (English wali)"),
]

for tag, voice, label in VOICES:
    mp3 = TMP / f"ab_{tag}_{voice}.mp3"
    if not mp3.exists() or mp3.stat().st_size == 0:
        t0 = time.time()
        _synthesize(LINE, mp3, voice)
        print(f"{tag} {label}: synthesized {mp3.stat().st_size}B in "
              f"{time.time() - t0:.1f}s", flush=True)
    t0 = time.time()
    _play_mp3_windows(str(mp3))
    print(f"{tag} {label}: played (startup {time.time() - t0:.2f}s)", flush=True)
    time.sleep(1.5)

print("done — order was A (Swara) -> B (Ava) -> C (Neerja)", flush=True)
