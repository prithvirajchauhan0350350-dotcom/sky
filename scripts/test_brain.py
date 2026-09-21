"""Probe OmniRoute brain: models catalog, native tool-calling, vision support."""
import base64
import json
import struct
import sys
import urllib.request
import zlib
from pathlib import Path


def png1x1_red():
    """Minimal valid 1x1 red PNG, built with stdlib only."""
    def chunk(typ, data):
        c = struct.pack(">I", len(data)) + typ + data
        return c + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00")
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", idat) + chunk(b"IEND", b""))


def call(body, token=None, timeout=90):
    hdrs = {"Content-Type": "application/json"}
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        "http://localhost:20128/v1/chat/completions",
        data=json.dumps(body).encode(), headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return {"http_error": e.code, "body": e.read().decode()[:300]}
    except Exception as e:
        return {"error": str(e)[:200]}


def token_from_settings():
    try:
        txt = (Path.home() / ".claude/settings.json").read_text()
        d = json.loads(txt)
        env = d.get("env", {})
        return env.get("ANTHROPIC_AUTH_TOKEN")
    except Exception:
        return None


print("=== a) model catalog (with token) ===")
tok = token_from_settings()
print("token found:", bool(tok))
req = urllib.request.Request("http://localhost:20128/v1/models",
                             headers={"Authorization": f"Bearer {tok}"} if tok else {})
try:
    with urllib.request.urlopen(req, timeout=15) as r:
        models = [m["id"] for m in json.load(r).get("data", [])]
    print(f"{len(models)} models:", models[:40])
except Exception as e:
    print("models endpoint failed:", str(e)[:150])
    models = []

print("\n=== b) native tool-calling (glm-5.3-flash) ===")
body = {
    "model": "glm-5.3-flash",
    "messages": [{"role": "user", "content": "What is 2+2? Use the calc tool."}],
    "tools": [{"type": "function", "function": {
        "name": "calc", "description": "Evaluate a math expression",
        "parameters": {"type": "object",
                       "properties": {"expr": {"type": "string"}},
                       "required": ["expr"]}}}],
    "max_tokens": 900,
}
r = call(body)
if "http_error" in r or "error" in r:
    print("tool test HTTP/error:", r)
else:
    m = r["choices"][0]["message"]
    tc = m.get("tool_calls")
    print("tool_calls:", json.dumps(tc)[:300] if tc else None)
    print("content:", repr((m.get("content") or "")[:200]))

print("\n=== c) vision (1x1 red pixel) ===")
b64 = base64.b64encode(png1x1_red()).decode()
cands = ["glm-5.3-flash"] + [m for m in models if any(
    k in m.lower() for k in ("4.5v", "4v", "vision", "vl", "4o", "gemini", "claude"))][:3]
for model in cands:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "What color is this image? One word."},
            {"type": "image_url",
             "image_url": {"url": f"data:image/png;base64,{b64}"}}]}],
        "max_tokens": 600,
    }
    r = call(body)
    if "http_error" in r or "error" in r:
        print(f"[{model}] FAIL:", str(r)[:160])
        continue
    m = r["choices"][0]["message"]
    c = (m.get("content") or "")
    if not c and m.get("reasoning_content"):
        c = "(reasoning only, no content)"
    print(f"[{model}] ->", c[:120].replace("\n", " "))
