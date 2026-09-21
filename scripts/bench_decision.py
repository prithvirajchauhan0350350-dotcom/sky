"""List models on the omni gateway + benchmark decision-style calls.

Benchmarks: given a small system prompt + one tool definition, how fast does
each model pick the right tool? Never prints the API key.
"""
import json
import os
import time

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env = {}
with open(os.path.join(ROOT, ".env"), encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")

BASE = env.get("OMNIROUTE_BASE_URL", "https://llms.sveltescan.com/v1").rstrip("/")
KEY = env.get("OMNIROUTE_API_KEY", "")
HEAD = {"Authorization": "Bearer " + KEY, "Content-Type": "application/json"}

r = httpx.get(BASE + "/models", headers=HEAD, timeout=20)
print("=== MODELS ===")
ids = [m.get("id") for m in r.json().get("data", [])]
for i in ids:
    print(i)

TOOLS = [
    {"type": "function", "function": {"name": "launch_app", "description": "Open an application by name.",
                                      "parameters": {"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]}}},
    {"type": "function", "function": {"name": "get_vitals", "description": "Get RAM/CPU/battery/vm stats of this PC.",
                                      "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "run_shell", "description": "Run a shell command on the PC.",
                                      "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]}}},
]
MSGS = [
    {"role": "system", "content": "You are SKY, a sharp assistant running on the user's Windows PC. Pick the right tool or answer briefly in Hinglish."},
    {"role": "user", "content": "calc kholo jaldi"},
]

CANDIDATES = [c for c in ("glm-5.3-flash", "qwen3.8-flash-next") if c in ids]
print("\n=== DECISION BENCH (2 turns each) ===")
for mdl in CANDIDATES:
    for turn in ("calc kholo jaldi", "RAM kitni hai?"):
        MSGS[1] = {"role": "user", "content": turn}
        t0 = time.time()
        try:
            rr = httpx.post(BASE + "/chat/completions", headers=HEAD,
                            json={"model": mdl, "messages": MSGS, "tools": TOOLS,
                                  "max_tokens": 700, "temperature": 0.4}, timeout=60)
            dt = (time.time() - t0) * 1000
            msg = rr.json().get("choices", [{}])[0].get("message", {})
            tc = msg.get("tool_calls") or []
            pick = tc[0]["function"]["name"] + "(" + str(tc[0]["function"].get("arguments"))[:40] + ")" if tc else "-"
            content = (msg.get("content") or "")[:50]
            reasoning = len(msg.get("reasoning_content") or "")
            print(f"[{dt:6.0f} ms] {mdl:22s} q={turn!r:40s} tool={pick} content={content!r} reasoning={reasoning}B")
        except Exception as e:
            print(f"[{mdl} q={turn!r}] EXC {str(e)[:100]}")
print("BENCH_DONE")
