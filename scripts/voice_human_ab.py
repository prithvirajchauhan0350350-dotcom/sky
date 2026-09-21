"""Human-feel A/B: expressive Neerja, tuned Neerja, Emma multilingual."""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import edge_tts  # noqa: E402
from core.voice import _play_mp3_windows  # noqa: E402

TMP = Path(__file__).resolve().parent.parent / "_voice_ab"

LINE = ("Haan bhai, main Sky hoon. Aaj ka test thoda alag hai — main apni "
        "awaaz thodi natural aur human jaisi bana rahi hoon. Dekho, jab main "
        "normal baat karti hoon, jaise ghar ke kaam ki baat, to awaaz aaram "
        "se aani chahiye. Aur jab koi kaam dene wali ho — jaldi, seedha, "
        "pakka. RAM kaafi bhari thi aaj, ab theek ho gayi. Batao, kaun si "
        "awaaz sabse real lag rahi hai?")

VARIANTS = [
    ("D", "en-IN-NeerjaExpressiveNeural", "+8%", "+0Hz"),
    ("E", "en-IN-NeerjaNeural", "+4%", "-2Hz"),
    ("F", "en-US-EmmaMultilingualNeural", "+8%", "+0Hz"),
]

async def synth(text, voice, out, rate, pitch):
    await edge_tts.Communicate(text, voice, rate=rate, pitch=pitch).save(str(out))

for tag, voice, rate, pitch in VARIANTS:
    mp3 = TMP / f"human_{tag}_{voice}.mp3"
    if not mp3.exists() or mp3.stat().st_size == 0:
        t0 = time.time()
        asyncio.run(synth(LINE, voice, mp3, rate, pitch))
        print(f"{tag} {voice} [{rate},{pitch}]: {mp3.stat().st_size}B "
              f"({time.time() - t0:.1f}s synth)", flush=True)
    _play_mp3_windows(str(mp3))
    print(f"{tag} played", flush=True)
    time.sleep(1.2)

print("done — order D (Neerja expressive) -> E (Neerja tuned) -> F (Emma)",
      flush=True)
