"""small + beam5 + initial_prompt: does domain conditioning fix vocab (Latin English words)?"""
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np  # noqa: E402
from core import voice  # noqa: E402
from faster_whisper import WhisperModel  # noqa: E402

TEXT = "Hey Sky, CPU tez chal raha hai. RAM kitni lagi hai batao jaldi."
tmp = Path(tempfile.mkdtemp(prefix="sky_wp_"))
mp3, wav = tmp / "t.mp3", tmp / "t.wav"
voice._synthesize(TEXT, mp3)
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3),
                "-ar", "16000", "-ac", "1", str(wav)], check=True)
raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(wav),
                      "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
                     capture_output=True, check=True).stdout
audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

PROMPT = ("Hinglish tech talk: Sky, CPU, RAM, disk, terminal, WiFi, battery, "
          "reminder, briefing. Kya bolu? Theek hai bhai.")
m = WhisperModel("small", device="cpu", compute_type="int8")
import time  # noqa: E402
t0 = time.time()
segs, info = m.transcribe(audio, beam_size=5, vad_filter=True,
                          condition_on_previous_text=False,
                          initial_prompt=PROMPT, language=None)
text = " ".join(s.text.strip() for s in segs).strip()
print(f"lang={info.language} {time.time()-t0:.1f}s heard: {text!r}")
subprocess.run(["rm", "-rf", str(tmp)])
