"""Verify the fast-path reply: 'open X' turns now skip the 2nd LLM call.
os.startfile is monkeypatched so nothing actually opens."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sky  # noqa: E402
from core import agent  # noqa: E402
from core.memory import Memory  # noqa: E402

agent.os.startfile = lambda target: None  # stub the real launch

cfg = sky.load_cfg()
mem = Memory(sky.ROOT / "data" / "memory.db")
t0 = time.time()
reply = sky.agent_turn(cfg, mem, "open calculator", confirm_fn=None)
dt = time.time() - t0
print(f"fast reply in {dt:.2f}s: {reply!r}")
assert "calc" in reply and len(reply) < 100, "fast path did not fire"
print("FAST_PATH_OK")
