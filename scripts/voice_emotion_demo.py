"""Emotion demo: per-sentence prosody (pitch/rate by mood), joined PCM.

Same voice (Neerja Expressive), but sentences get mood-matched prosody:
greeting=bright, news=excited, warning=serious, closing=warm.
Segments are synthesized in parallel, decoded, concatenated, played once.
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import edge_tts  # noqa: E402
import miniaudio  # noqa: E402
import numpy as np  # noqa: E402
import sounddevice as sd  # noqa: E402

TMP = Path(__file__).resolve().parent.parent / "_voice_ab"
VOICE = "en-IN-NeerjaExpressiveNeural"

# (text, rate, pitch) — mood per sentence
SEGMENTS = [
    ("Namaste bhai! Good morning! Uth gaye?", "+10%", "+4Hz"),
    ("Ek badi news hai — tumhara Sky ab ek dum naya ho gaya hai!", "+14%", "+5Hz"),
    ("Ek serious baat. Kal RAM ninety-four percent bhari thi. Thoda dhyan dena.", "+2%", "-3Hz"),
    ("Waise chai pee lo. Main yahin hoon — jo chahiye, bas bolo.", "+5%", "+1Hz"),
]

GAP_FRAMES = int(48000 * 0.35)  # 350ms pause between sentences


async def synth(text, rate, pitch, out):
    await edge_tts.Communicate(text, VOICE, rate=rate, pitch=pitch).save(str(out))


def decode(path):
    d = miniaudio.decode_file(str(path), nchannels=2, sample_rate=48000,
                              output_format=miniaudio.SampleFormat.SIGNED16)
    return np.asarray(d.samples, dtype=np.int16).reshape(-1, 2)


async def main():
    files = []
    for i, (text, rate, pitch) in enumerate(SEGMENTS):
        out = TMP / f"emo_{i}_{rate}_{pitch}.mp3"
        if not out.exists() or out.stat().st_size == 0:
            await edge_tts.Communicate(text, VOICE, rate=rate,
                                       pitch=pitch).save(str(out))
        files.append(out)
    parts = [decode(p) for p in files]
    gap = np.zeros((GAP_FRAMES, 2), dtype=np.int16)
    joined = np.concatenate([p for tup in zip(parts, [gap] * len(parts)) for p in tup])
    t0 = time.time()
    sd.play(joined, 48000)
    sd.wait()
    print(f"played {len(parts)} segments, {joined.shape[0] / 48000:.1f}s, "
          f"startup {time.time() - t0:.2f}s", flush=True)

asyncio.run(main())
