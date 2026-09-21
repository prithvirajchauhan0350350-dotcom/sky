"""Probe which payload key disables GLM thinking on the omni route.

Tries several conventions, reports content + reasoning_content presence.
Never prints the API key.
"""
import json
import os
import sys
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
if not KEY:
    print("NO KEY FOUND in .env (keys present: %s)" % sorted(env))
    sys.exit(1)

PROMPT = ("Is 97 prime? Give the answer and one short reason.")
MSGS = [{"role": "user", "content": PROMPT}]

VARIANTS = [
    ("baseline (thinking on)", {}),
    ("thinking:{type:disabled}", {"thinking": {"type": "disabled"}}),
    ("chat_template_kwargs.enable_thinking=false", {"chat_template_kwargs": {"enable_thinking": False}}),
    ("reasoning_effort=none", {"reasoning_effort": "none"}),
    ("enable_thinking=false", {"enable_thinking": False}),
]

URL = BASE + "/chat/completions"
HEAD = {"Authorization": "Bearer " + KEY, "Content-Type": "application/json"}

for name, extra in VARIANTS:
    body = {"model": "glm-5.3-flash", "messages": MSGS, "max_tokens": 350, "temperature": 0.3}
    body.update(extra)
    t0 = time.time()
    try:
        r = httpx.post(URL, headers=HEAD, json=body, timeout=45)
        dt = (time.time() - t0) * 1000
        data = r.json()
        msg = data.get("choices", [{}])[0].get("message", {})
        content = (msg.get("content") or "").strip()
        reasoning = (msg.get("reasoning_content") or msg.get("reasoning") or "") or ""
        rc = (msg.get("reasoning_content") is not None, msg.get("reasoning") is not None)
        print(f"[{dt:6.0f} ms] {name}: http={r.status_code} content={len(content)}B "
              f"reasoning={len(str(reasoning))}B has_rc_field={rc}")
        print(f"    content[:70]={content[:70]!r}")
        if r.status_code != 200:
            print(f"    body[:150]={r.text[:150]!r}")
    except Exception as e:
        print(f"[{name}] EXC: {str(e)[:120]}")
print("PROBE_DONE")
