"""Verify deep_knowledge retrieval from the Part II encyclopedia."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import agent  # noqa: E402

for q in ("black hole time dilation", "印度 basic structure doctrine", "quantum entanglement",
          "how does compound interest work", "fever first aid burn treatment",
          "sql injection testing web app", "nmap scan types", "linux privilege escalation",
          "wifi wpa2 handshake crack", "how to report a bug bounty finding",
          "python decorators explained", "reverse engineering malware",
          "burp suite repeater tool", "intruder cluster bomb attack",
          "burp collaborator out of band detection", "burp session handling macro csrf token"):
    t0 = time.time()
    out = agent.t_deep_knowledge(q)
    dt = (time.time() - t0) * 1000
    first = out.splitlines()[0] if out else "(empty)"
    print(f"[{dt:5.1f} ms] {q!r} -> {out[:60]!r}... ({len(out)} chars)")
    assert "ERROR" not in out, out
print("DEEP_KNOWLEDGE_OK")
