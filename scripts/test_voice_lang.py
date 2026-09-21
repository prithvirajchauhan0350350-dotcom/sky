"""Voice language test: pick_voice routing + both Indian voices synthesize +
whisper round-trip (Hindi clip transcribes in Devanagari)."""
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np  # noqa: E402

from core import voice  # noqa: E402

CASES = [
    ("All systems nominal, sir.", voice.VOICE_EN),
    ("aaj weather kaisa hai sir?", voice.VOICE_HI),
    ("मुझे याद दिलाओ कि पानी पीना है", voice.VOICE_HI),
    ("open the calculator for me", voice.VOICE_EN),
    ("bhai mood kaisa hai aaj", voice.VOICE_HI),
    ("status report bhejo", voice.VOICE_HI),
    ("hello sir", voice.VOICE_EN),
]
fails = 0
for text, want in CASES:
    got = voice.pick_voice(text)
    ok = got == want
    fails += 0 if ok else 1
    print(f"{'OK ' if ok else 'FAIL'} {got:22s} <- {text!r}")
print(f"routing: {'PASS' if fails == 0 else f'{fails} FAILS'} ({len(CASES)} cases)")

tmp = Path(tempfile.mkdtemp(prefix="sky_voice_"))
for label, text in [("en-IN", "Sky reporting for duty, sir."),
                    ("hi-IN", "सर, सब सिस्टम ठीक हैं।")]:
    mp3, wav = tmp / "t.mp3", tmp / "t.wav"
    voice._synthesize(text, mp3)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3),
                    "-ar", "16000", "-ac", "1", str(wav)], check=True)
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(wav),
                          "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
                         capture_output=True, check=True).stdout
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    said = voice.transcribe(audio)
    print(f"roundtrip {label}: {said!r}")

subprocess.run(["rm", "-rf", str(tmp)])
