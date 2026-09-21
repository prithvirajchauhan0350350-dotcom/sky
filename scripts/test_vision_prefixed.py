"""Probe prefixed vision model ids in OmniRoute."""
from pathlib import Path
import base64
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_brain import png1x1_red, call  # noqa: E402

b64 = base64.b64encode(png1x1_red()).decode()
cands = ["glm/glm-4.5v", "glmt/glm-4.5v", "glm/glm-4.6v", "glmt/glm-4.6v",
         "glm/glm-5v", "glm/glm-5.3-flash", "t3chat/claude-sonnet-4",
         "vp/claude-sonnet-4", "in-ai/gemini-2.5-flash", "t3chat/gemini-2.5-flash",
         "t3chat/gpt-4o", "vp/gpt-4o-mini"]
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
        print(f"[{model}] HTTP {r['http_error']}: {r['body'][:110]}")
    elif "error" in r:
        print(f"[{model}] ERR: {r['error'][:110]}")
    else:
        m = r["choices"][0]["message"]
        c = (m.get("content") or "(no content)")[:90].replace("\n", " ")
        print(f"[{model}] OK -> {c}")
