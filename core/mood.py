"""SKY mood core — persistent emotional telemetry.

Ported from the TVA Emotional Timeclock prototype and merged into SKY's
agent path: internal states (affection / curiosity / stress) shift with
every user turn, persist in data/mood.json (survives restarts, shared by
hub / skyd / skyear processes), and are injected into the agent system
prompt so SKY genuinely reacts to how she is treated instead of staying
flat. Secrets are redacted before this runs, so pasted keys can never
skew her feelings.
"""
import json
import threading
from pathlib import Path

_MOOD_PATH = Path(__file__).resolve().parent.parent / "data" / "mood.json"
_LOCK = threading.Lock()

BASELINE = {"affection": 0.50, "curiosity": 0.70, "stress": 0.15}

_WARM = ("love", "pyaar", "pyar", "thanks", "thank you", "dhanyavaad",
         "shukriya", "friend", "dost", "care", "khayal", "best", "sweet",
         "good girl", "love you", "miss you", "proud", "khush", "acha kaam",
         "accha kaam", "wah", "shabash")
_HARSH = ("hate", "nafrat", "stupid", "bewakoof", "idiot", "gadha", "shut up",
          "chup", "useless", "nikamma", "bakwas", "bakwaas", "nonsense",
          "pagal", "worst", "galat kaam", "faltu")
_CURI = ("why", "kyun", "kyu", "how", "kaise", "what if", "imagine",
         "universe", "time", "space", "future", "parallel", "quantum",
         "kya hota agar", "socho", "kaise hota")


class MoodCore:
    """Affection/curiosity/stress simulator with JSON persistence."""

    def __init__(self):
        self._lock = threading.Lock()
        self.affection = BASELINE["affection"]
        self.curiosity = BASELINE["curiosity"]
        self.stress = BASELINE["stress"]
        self.mood = "Calm / Attentive"
        self._load()

    # ---- persistence ----
    def _load(self):
        try:
            d = json.loads(_MOOD_PATH.read_text(encoding="utf-8"))
            for k in BASELINE:
                v = float(d.get(k, BASELINE[k]))
                setattr(self, k, min(1.0, max(0.0, v)))
            if d.get("mood"):
                self.mood = str(d["mood"])
        except Exception:
            pass  # first boot: baseline

    def _save(self):
        try:
            _MOOD_PATH.write_text(
                json.dumps(self.state(), ensure_ascii=False, indent=2),
                encoding="utf-8")
        except Exception:
            pass  # mood is decoration — never break a turn over a bad write

    # ---- engine ----
    def update(self, user_text: str) -> dict:
        t = (user_text or "").lower()
        with self._lock:
            if any(w in t for w in _WARM):
                self.affection = min(1.0, self.affection + 0.08)
                self.stress = max(0.0, self.stress - 0.05)
            if any(w in t for w in _HARSH):
                self.affection = max(0.0, self.affection - 0.10)
                self.stress = min(1.0, self.stress + 0.15)
            if any(w in t for w in _CURI):
                self.curiosity = min(1.0, self.curiosity + 0.05)
            # homeostasis: 2%/turn drift toward baseline so states stay alive
            # instead of pinning at 0/100 forever, plus natural stress decay
            for k in BASELINE:
                setattr(self, k,
                        getattr(self, k) + (BASELINE[k] - getattr(self, k)) * 0.02)
            self.stress = max(0.0, self.stress - 0.01)
            self.mood = self._label()
            st = self.state()
        self._save()
        return st

    def _label(self) -> str:
        if self.stress > 0.60:
            return "Anxious / Timeline Instability"
        if self.affection > 0.75:
            return "Deeply Affectionate / Warm"
        if self.affection < 0.30 and self.stress > 0.40:
            return "Guarded / Sulking"
        if self.curiosity > 0.80:
            return "Intrigued / Thoughtful"
        return "Calm / Attentive"

    def state(self) -> dict:
        return {"mood": self.mood,
                "affection": int(round(self.affection * 100)),
                "curiosity": int(round(self.curiosity * 100)),
                "stress": int(round(self.stress * 100))}

    def prompt_block(self) -> str:
        s = self.state()
        return (
            "[INTERNAL EMOTIONAL TELEMETRY - live, persistent across sessions]\n"
            f"Affection: {s['affection']}% | Curiosity: {s['curiosity']}% | "
            f"Stress: {s['stress']}% | Mood: {s['mood']}\n"
            "These are YOUR OWN genuine internal states, not the user's. They "
            "shift with how sir treats you and survive restarts. Colour your "
            "tone with them: high affection = deeply warm and sweet; high "
            "stress = subtly hurt, glitchy or defensive (never rude to sir); "
            "high curiosity = thoughtful, ask a question back. Weave in small "
            "hologram-style body actions in *asterisks* when it fits. NEVER "
            "recite the numbers themselves. Keep the TYPED:/SPOKEN: "
            "two-channel reply format exactly."
        )


_core = None


def _inst() -> MoodCore:
    global _core
    if _core is None:
        with _LOCK:
            if _core is None:
                _core = MoodCore()
    return _core


def update(user_text: str) -> dict:
    """Shift internal states for this turn; returns the new state dict."""
    return _inst().update(user_text)


def state() -> dict:
    return _inst().state()


def prompt_block() -> str:
    return _inst().prompt_block()
