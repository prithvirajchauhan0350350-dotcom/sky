"""Find a vision model in OmniRoute's live catalog."""
from pathlib import Path
import base64
import json
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_brain import png1x1_red, call  # noqa: E402

b64 = base64.b64encode(png1x1_red()).decode()
cands = ["gpt-4o-mini", "gpt-4o", "gpt-5", "gpt-5.6", "gpt-5.6-mini", "gpt-6",
         "glm-4.5v", "glm-4.6v", "glm-5v", "qwen2.5-vl-7b", "claude-sonnet-4",
         "gemini-2.5-flash", "gpt-6-astra"]
for model in cands:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "What color is this image? One word."},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]}],
        "max_tokens": 500,
    }
    r = call(body, timeout=60)
    if "http_error" in r:
        print(f"[{model}] HTTP {r['http_error']}: {r['body'][:120]}")
    elif "error" in r:
        print(f"[{model}] ERR: {r['error'][:120]}")
    else:
        m = r["choices"][0]["message"]
        c = (m.get("content") or "(no content)")[:90].replace("\n", " ")
        print(f"[{model}] OK -> {c}")
