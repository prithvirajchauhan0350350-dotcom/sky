"""Benchmark the REAL production-sized prompt: persona.md + knowledge[:6000]
+ realistic tools, on both gateway models. Measures per-iteration latency.
Never prints the API key.
"""
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

persona = open(os.path.join(ROOT, "persona.md"), encoding="utf-8").read()
knowledge = open(os.path.join(ROOT, "knowledge.txt"), encoding="utf-8").read()
SYSTEM = persona + "\n\n" + knowledge[:6000]
print(f"system prompt: {len(SYSTEM)} chars (~{len(SYSTEM)//4} tokens approx)")

TOOLS = [
    {"type": "function", "function": {"name": "launch_app", "description": "Open an app by name (calc, chrome, notepad...).", "parameters": {"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]}}},
    {"type": "function", "function": {"name": "get_vitals", "description": "RAM/CPU/battery/disk stats.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "run_shell", "description": "Run a shell command (needs confirm for risky).", "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]}}},
    {"type": "function", "function": {"name": "get_screen_info", "description": "Describe what's on screen (window titles, focused app).", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "set_reminder", "description": "Set a reminder (natural time text).", "parameters": {"type": "object", "properties": {"text": {"type": "string"}, "time": {"type": "string"}}, "required": ["text", "time"]}}},
    {"type": "function", "function": {"name": "deep_knowledge", "description": "Search the deep encyclopedia for how/why questions.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
]

QUERIES = [
    ("tool-turn", "RAM kitni hai abhi?"),
    ("no-tool-turn", "arre yaar aaj bahut thak gaya hoon"),
]

for mdl in ("glm-5.3-flash", "deepseek-v4.1-flash"):
    for label, q in QUERIES:
        msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": q}]
        t0 = time.time()
        try:
            r = httpx.post(BASE + "/chat/completions", headers=HEAD,
                           json={"model": mdl, "messages": msgs, "tools": TOOLS,
                                 "max_tokens": 2000, "temperature": 0.6}, timeout=90)
            dt = (time.time() - t0) * 1000
            msg = r.json().get("choices", [{}])[0].get("message", {})
            tc = msg.get("tool_calls") or []
            pick = tc[0]["function"]["name"] if tc else "-"
            content = (msg.get("content") or "").replace("\n", " ")[:90]
            reasoning = len(msg.get("reasoning_content") or "")
            print(f"[{dt:6.0f} ms] {mdl:20s} {label:12s} tool={pick:18s} reasoning={reasoning:5d}B  {content!r}")
        except Exception as e:
            print(f"[{mdl} {label}] EXC {str(e)[:120]}")
print("FATBENCH_DONE")
