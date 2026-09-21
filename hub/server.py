"""SKY ASSISTANT HUB — web interface for SKY, running on SKY's own venv.

Bridges the browser UI to the real brain (sky.agent_turn on OmniRoute/GLM),
real memory.db, vitals, reminders, and the real JARVIS voice (edge-tts).
Risky tool calls pause for a Yes/No click in the UI (confirm bridge).

Run:  .venv\\Scripts\\python.exe hub\\server.py   ->  http://127.0.0.1:20130
"""
import hashlib
import json
import logging
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sky  # noqa: E402
from core import agency, mood, persona, telemetry, voice  # noqa: E402
from core.memory import Memory  # noqa: E402

import automation  # noqa: E402  (spy modules live at SKY root)
import spy_data  # noqa: E402
import spy_ops  # noqa: E402
import spy_sec  # noqa: E402
import spy_vitals  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")
# pythonw has no console — mirror logs to a file so hub errors are never lost
try:
    _fh = logging.FileHandler(ROOT / "logs" / "hub.log", encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(_fh)
except Exception:
    pass
log = logging.getLogger("sky.hub")

CFG = sky.load_cfg()
PORT = 20130
BIND = "127.0.0.1"
CONFIRM_TIMEOUT = 120
TTS_CACHE = ROOT / "hub" / "tts_cache"

PENDING = {}          # id -> {cmd, detail, event, answer}
PLOCK = threading.Lock()


def confirm_fn(cmd, detail=""):
    """Bridge agent tool-confirmation to the web UI (blocks this request
    thread until the user clicks Yes/No or 120s timeout)."""
    cid = uuid.uuid4().hex[:10]
    ev = threading.Event()
    with PLOCK:
        PENDING[cid] = {"cmd": str(cmd), "detail": str(detail), "event": ev,
                        "answer": None, "ts": time.time()}
    log.info("confirm requested %s: %s", cid, str(cmd)[:120])
    got = ev.wait(CONFIRM_TIMEOUT)
    with PLOCK:
        rec = PENDING.pop(cid, None)
    if not got or rec is None or rec["answer"] is None:
        return False
    return bool(rec["answer"])


_DSTATUS = {"ts": 0.0, "data": None}


def _daemon_status():
    """Cached process scan (10s TTL) — full psutil sweep during an active LLM
    call used to stall /api/health into timeouts. Cache keeps health instant."""
    now = time.time()
    cached = _DSTATUS["data"]
    if cached is not None and now - _DSTATUS["ts"] < 10:
        return cached
    daemons = {"skyear": False, "skyd": False}
    try:
        import psutil
        for p in psutil.process_iter(["cmdline"]):
            try:
                cl = " ".join(p.info["cmdline"] or []).lower()
                if "skyear.py" in cl:
                    daemons["skyear"] = True
                if "skyd.py" in cl:
                    daemons["skyd"] = True
            except Exception:
                pass
    except Exception as e:
        log.warning("daemon check failed: %s", e)
    _DSTATUS["ts"] = now
    _DSTATUS["data"] = daemons
    return daemons


def api_health():
    brain = CFG.get("brain", {}).get("active", "omni")
    prof = (CFG.get("brain", {}).get("profiles") or {}).get(brain) or {}
    model = prof.get("model", "?")
    return {"ok": True, "daemons": _daemon_status(), "brain": brain, "model": model}


def api_chat(text):
    text = (text or "").strip()
    if not text:
        return {"error": "empty"}
    if len(text) > int(CFG.get("input_char_cap", 4000)):
        text = text[:int(CFG.get("input_char_cap", 4000))]
    mem = Memory(ROOT / "data" / "memory.db")  # sqlite per-thread, like sky_hud
    try:
        t0 = time.time()
        reply = sky.agent_turn(CFG, mem, text, confirm_fn)
        say_text, show_text = persona.split_bilingual(reply)
        log.info("turn done in %.1fs chars=%d", time.time() - t0, len(show_text))
        return {"reply": show_text, "spoken": say_text, "mood": mood.state()}
    except Exception as e:
        log.exception("chat failed")
        return {"error": str(e)[:200]}


def api_agency():
    r = agency.roster(ROOT)
    return {"total": len(r.get("agents", [])),
            "divisions": r.get("divisions", []),
            "agents": r.get("agents", []),
            "active": agency.active(ROOT)}


def api_agency_agent(qs):
    slug = (parse_qs(qs).get("slug", [""])[0] or "").strip()
    r = agency.roster(ROOT)
    meta = next((a for a in r.get("agents", []) if a["slug"] == slug), None)
    if meta is None:
        return {"error": "unknown specialist"}
    f = ROOT / "agency" / meta["file"]
    if not f.is_file():
        return {"error": "specialist file missing"}
    return {"meta": meta,
            "body": f.read_text(encoding="utf-8", errors="replace")}


def api_agency_activate(payload):
    slug = str(payload.get("slug", "")).strip()
    try:
        rec = agency.activate(ROOT, slug)
    except ValueError as e:
        return {"error": str(e)[:120]}
    log.info("agency specialist activated: %s", slug)
    return {"ok": True, "active": rec,
            "message": f"Specialist active: {rec['name']}"}


def api_agency_deactivate():
    ok = agency.deactivate(ROOT)
    log.info("agency specialist stood down (was active: %s)", ok)
    return {"ok": True, "message": "Standing down — plain SKY restored."}


def api_tts(qs):
    text = (parse_qs(qs).get("text", [""])[0] or "").strip()[:500]
    if not text:
        return None, "empty text"
    TTS_CACHE.mkdir(exist_ok=True)
    v = parse_qs(qs).get("voice", [None])[0]
    mp3 = TTS_CACHE / (hashlib.sha1(f"{v}|{text}".encode()).hexdigest()[:16] + ".mp3")
    if not mp3.exists() or mp3.stat().st_size == 0:
        try:
            rate, pitch = voice._pick_mood(text)  # prosody matched to the line
            voice._synthesize(text, mp3, v, rate=rate, pitch=pitch)
        except Exception as e:
            log.exception("tts failed")
            return None, str(e)[:150]
    return mp3, None


def api_reminders():
    mem = Memory(ROOT / "data" / "memory.db")
    try:
        rows = mem.pending_reminders()[:15]
        out = [{"id": r[0], "text": r[1], "due": r[2]} for r in rows]
        return {"reminders": out}
    except Exception as e:
        return {"error": str(e)[:150]}
    finally:
        try:
            mem.conn.close()
        except Exception:
            pass


SPY_SIM_KINDS = {"projectile", "pendulum", "spring", "cooling", "orbit"}

# ---- SKY FILES: read-only browser over the SKY tree + memory viewer ----

_TEXT_EXT = {".py", ".md", ".txt", ".json", ".bat", ".log", ".yaml", ".yml",
             ".csv", ".cfg", ".ini", ".html", ".js", ".css", ".env", ".toml"}
_HIDE_DIRS = {".venv", "venv", "__pycache__", "tts_cache", "backup",
              ".git", "node_modules", "spy_screenshots"}
_CT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
       ".gif": "image/gif", ".mp3": "audio/mpeg", ".wav": "audio/wav",
       ".mp4": "video/mp4", ".webm": "video/webm", ".m4v": "video/mp4",
       ".css": "text/css", ".js": "application/javascript",
       ".html": "text/html; charset=utf-8",
       ".json": "application/json",
       ".moc3": "application/octet-stream",
       ".db": "application/octet-stream"}

# ---- IRON OS: Live2D anime avatar (hub/live2d/) + vendored JS (hub/static/) ----
_LIVE2D_DIR = Path(__file__).parent / "live2d"
_LIVE2D_EXT = {".json", ".moc3", ".png", ".jpg", ".jpeg", ".motion3", ".exp3"}
_STATIC_DIR = Path(__file__).parent / "static"
_STATIC_EXT = {".js", ".css", ".png", ".woff2"}

# ---- SKY AVATAR: photoreal portrait + pre-rendered lip-synced clips ----
_AVATAR_DIR = ROOT / "avatar"
_AVATAR_EXT = {".jpg", ".jpeg", ".png", ".mp4", ".webm", ".m4v"}


def _avatar_file(sub: str):
    """Resolve a bare filename under avatar/ (no dirs, no traversal)."""
    name = Path(sub.replace("\\", "/")).name  # strip any path parts
    if name in ("portrait.jpg", "portrait.jpeg", "portrait"):
        f = _AVATAR_DIR / "sky_portrait.jpg"
    else:
        f = _AVATAR_DIR / name
        if not f.is_file():
            f = _AVATAR_DIR / "clips" / name
    if f.suffix.lower() not in _AVATAR_EXT or not f.is_file():
        return None
    return f


def api_avatar() -> dict:
    """What the UI can show: portrait + any pre-rendered clips."""
    clips = []
    cdir = _AVATAR_DIR / "clips"
    if cdir.is_dir():
        clips = sorted(f.name for f in cdir.glob("*.mp4")
                       if f.stat().st_size > 10_000 and not f.name.startswith("temp_"))
    return {"portrait": "/avatar/portrait.jpg", "clips": clips,
            "greeting": next((c for c in clips if "hello" in c or "greet" in c),
                             clips[0] if clips else None)}


def _serve_file(self, f: Path, cache: str = "max-age=3600"):
    body = f.read_bytes()
    self.send_response(200)
    self.send_header("Content-Type", _CT.get(f.suffix.lower(),
                                             "application/octet-stream"))
    self.send_header("Content-Length", str(len(body)))
    self.send_header("Cache-Control", cache)
    self.end_headers()
    self.wfile.write(body)



def _safe_root_path(rel: str) -> Path | None:
    """Resolve rel under ROOT; None if it escapes the tree (no .., no absolute)."""
    if not rel:
        return ROOT
    p = Path(rel.replace("\\", "/"))
    if p.is_absolute() or ".." in p.parts:
        return None
    full = (ROOT / p).resolve()
    try:
        full.relative_to(ROOT.resolve())
    except ValueError:
        return None
    return full


def api_files(rel: str = "") -> dict:
    base = _safe_root_path(rel)
    if base is None or not base.is_dir():
        return {"error": "not a directory"}
    dirs, files = [], []
    for e in sorted(base.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
        if e.is_dir():
            if e.name in _HIDE_DIRS or e.name.startswith("."):
                continue
            dirs.append(e.name)
        else:
            try:
                st = e.stat()
                files.append({"name": e.name, "size": st.st_size,
                              "mtime": int(st.st_mtime)})
            except OSError:
                pass
    return {"path": rel, "dirs": dirs, "files": files,
            "pins": ["persona.md", "knowledge.txt", "config.json",
                     "requirements.txt", "README.md", "logs/sky.log",
                     "logs/skyd.log", "data/os_actions.log"]}


def api_file(rel: str) -> dict:
    f = _safe_root_path(rel)
    if f is None or not f.is_file():
        return {"error": "not a file"}
    st = f.stat()
    if st.st_size > 300_000:
        return {"error": f"file too large to preview ({st.st_size} bytes) — use raw link"}
    if f.suffix.lower() in _CT and f.suffix.lower() not in (".log",):
        return {"binary": True, "size": st.st_size, "kind": f.suffix.lower()}
    try:
        text = f.read_text(errors="replace")
    except Exception as e:
        return {"error": str(e)[:150]}
    return {"path": rel, "size": st.st_size, "text": text}


def api_raw(rel: str):
    f = _safe_root_path(rel)
    if f is None or not f.is_file():
        return None, 404
    return f, 200


def api_memory() -> dict:
    mem = Memory(ROOT / "data" / "memory.db")
    try:
        facts = [{"id": r[0], "kind": r[1], "key": r[2] or "", "value": r[3]}
                 for r in mem.conn.execute(
                     "SELECT id, kind, key, value FROM facts ORDER BY id DESC LIMIT 250")]
        msgs = [{"role": r[0], "content": r[1]} for r in mem.conn.execute(
            "SELECT role, content FROM messages ORDER BY id DESC LIMIT 60")]
        n_facts = mem.conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        n_msgs = mem.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        n_rem = len(mem.pending_reminders())
        return {"facts": facts, "messages": msgs,
                "counts": {"facts": n_facts, "messages": n_msgs,
                           "reminders": n_rem}}
    except Exception as e:
        return {"error": str(e)[:200]}
    finally:
        try:
            mem.conn.close()
        except Exception:
            pass


def api_spy(payload: dict):
    """Direct manual control of the Spy modules — no LLM in the loop.
    payload: {op: ..., ...params}. Everything the user clicks runs exactly once."""
    op = payload.pop("op", "")
    p = payload
    try:
        if op == "vitals":
            return spy_vitals.get_vitals()
        if op == "diagnose":
            return spy_vitals.diagnose()
        if op == "security_scan":
            return spy_sec.security_scan()
        if op == "net_scan":
            return spy_sec.scan_network(cidr=p.get("cidr", ""))
        if op == "hash":
            algos = (p.get("algorithm") or "sha256",)
            return spy_sec.hash_file(p.get("path", ""), algorithms=algos)
        if op == "pw_strength":
            return spy_sec.password_strength(p.get("pw", ""))
        if op == "encrypt":
            return spy_sec.encrypt_file(p.get("path", ""), p.get("password", ""),
                                        delete_original=bool(p.get("delete_original", False)))
        if op == "decrypt":
            return spy_sec.decrypt_file(p.get("path", ""), p.get("password", ""))
        if op == "analyse":
            return spy_data.analyse(p.get("path", ""))
        if op == "simulate":
            kind = p.pop("kind", "projectile")
            if kind not in SPY_SIM_KINDS:
                return {"error": f"unknown simulation kind {kind!r}"}
            return spy_data.simulate(kind, **p)
        if op == "organize":
            return spy_ops.organize_folder(p.get("path", ""),
                                           apply=bool(p.get("apply", False)))
        if op == "send_email":
            return spy_ops.send_email(p.get("to", ""), p.get("subject", ""),
                                      p.get("body", ""))
        if op == "smart_home":
            return spy_ops.smart_home(device=p.get("device", ""),
                                      action=p.get("action", ""),
                                      value=p.get("value"))
        if op == "screenshot":
            return automation.take_screenshot()
        if op == "active_window":
            return {"window": automation.active_window_title()}
        if op == "type_text":
            return automation.type_text(p.get("text", ""))
        if op == "click":
            return automation.click_at(int(p.get("x", 0)), int(p.get("y", 0)),
                                       button=p.get("button", "left"),
                                       double=bool(p.get("double", False)))
        if op == "press_key":
            return automation.press_key(p.get("key", ""))
        if op == "open_app":
            return automation.open_app(p.get("name", ""))
        if op == "audit_log":
            f = ROOT / "data" / "os_actions.log"
            if not f.exists():
                return {"log": "(empty)"}
            lines = f.read_text(errors="replace").splitlines()[-120:]
            return {"log": "\n".join(lines) or "(empty)"}
        if op == "list_screenshots":
            d = ROOT / "spy_screenshots"
            files = sorted(d.glob("*.png"), key=lambda x: x.stat().st_mtime)[-12:]
            return {"shots": [f.name for f in reversed(files)]}
        return {"error": f"unknown op {op!r}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"[:220]}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quiet default access spam
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        try:
            if u.path in ("/", "/index.html"):
                p = Path(__file__).parent / "index.html"
                body = p.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif u.path in ("/spy", "/spy.html"):
                p = Path(__file__).parent / "spy.html"
                body = p.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif u.path in ("/files", "/files.html"):
                p = Path(__file__).parent / "files.html"
                body = p.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif u.path in ("/iron", "/iron.html"):
                _serve_file(self, Path(__file__).parent / "iron.html", cache="no-store")
            elif u.path == "/iron.css":
                _serve_file(self, Path(__file__).parent / "iron.css", cache="no-store")
            elif u.path == "/iron.js":
                _serve_file(self, Path(__file__).parent / "iron.js", cache="no-store")
            elif u.path in ("/roam", "/roam.html"):
                _serve_file(self, Path(__file__).parent / "roam.html", cache="no-store")
            elif u.path == "/skyface.js":
                _serve_file(self, Path(__file__).parent / "skyface.js", cache="no-store")
            elif u.path == "/tva":
                _serve_file(self, Path(__file__).parent / "tva.html", cache="no-store")
            elif u.path.startswith("/raw/"):
                rel = u.path[len("/raw/"):]
                f, code = api_raw(rel)
                if code != 200 or f is None:
                    self._json({"error": "not found"}, 404)
                    return
                body = f.read_bytes()
                ct = _CT.get(f.suffix.lower(), "application/octet-stream")
                self.send_response(200)
                self.send_header("Content-Type", ct)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif u.path.startswith("/spyshots/"):
                name = Path(u.path).name
                f = ROOT / "spy_screenshots" / name
                if f.is_file() and f.suffix == ".png":
                    body = f.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self._json({"error": "not found"}, 404)
            elif u.path.startswith("/avatar/"):
                f = _avatar_file(u.path[len("/avatar/"):])
                if f is None:
                    self._json({"error": "not found"}, 404)
                    return
                _serve_file(self, f)
            elif u.path.startswith("/static/"):
                name = Path(u.path).name  # bare filename only — traversal-safe
                f = _STATIC_DIR / name
                if f.is_file() and f.suffix.lower() in _STATIC_EXT:
                    _serve_file(self, f)
                else:
                    self._json({"error": "not found"}, 404)
            elif u.path.startswith("/live2d/"):
                rel = u.path[len("/live2d/"):]
                f = _LIVE2D_DIR / rel.replace("\\", "/")
                try:
                    f = f.resolve()
                    f.relative_to(_LIVE2D_DIR.resolve())
                except (ValueError, OSError):
                    self._json({"error": "forbidden"}, 403)
                    return
                if f.is_file() and f.suffix.lower() in _LIVE2D_EXT:
                    _serve_file(self, f, cache="no-store")
                else:
                    self._json({"error": "not found"}, 404)
            elif u.path == "/api/avatar":
                self._json(api_avatar())
            elif u.path == "/api/health":
                self._json(api_health())
            elif u.path == "/api/mood":
                self._json(mood.state())
            elif u.path == "/api/agency":
                self._json(api_agency())
            elif u.path == "/api/agency/agent":
                self._json(api_agency_agent(u.query))
            elif u.path == "/api/vitals":
                self._json({"report": telemetry.report(), "sys": telemetry.snapshot()})
            elif u.path == "/api/reminders":
                self._json(api_reminders())
            elif u.path == "/api/files":
                q = parse_qs(u.query)
                self._json(api_files(q.get("path", [""])[0]))
            elif u.path == "/api/file":
                q = parse_qs(u.query)
                self._json(api_file(q.get("path", [""])[0]))
            elif u.path == "/api/memory":
                self._json(api_memory())
            elif u.path == "/api/pending":
                with PLOCK:
                    pend = [{"id": k, "cmd": v["cmd"], "detail": v["detail"]}
                            for k, v in PENDING.items()]
                self._json({"pending": pend})
            elif u.path == "/api/tts":
                mp3, err = api_tts(u.query)
                if err:
                    self._json({"error": err}, 400)
                    return
                body = mp3.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "audio/mpeg")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "max-age=86400")
                self.end_headers()
                self.wfile.write(body)
            else:
                self._json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            log.exception("GET %s failed", u.path)
            self._json({"error": str(e)[:200]}, 500)

    def do_POST(self):
        u = urlparse(self.path)
        try:
            n = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(n) or b"{}")
            if u.path == "/api/chat":
                self._json(api_chat(payload.get("text", "")))
            elif u.path == "/api/agency/activate":
                self._json(api_agency_activate(payload))
            elif u.path == "/api/agency/deactivate":
                self._json(api_agency_deactivate())
            elif u.path == "/api/spy":
                self._json(api_spy(payload))
            elif u.path == "/api/confirm":
                cid = payload.get("id", "")
                ans = bool(payload.get("answer", False))
                with PLOCK:
                    rec = PENDING.get(cid)
                    if rec:
                        rec["answer"] = ans
                        rec["event"].set()
                self._json({"ok": rec is not None})
            else:
                self._json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            log.exception("POST %s failed", u.path)
            self._json({"error": str(e)[:200]}, 500)


def main():
    from core import single_instance
    if not single_instance("Local\\SKY_HUB_SINGLETON"):
        log.info("hub already running — exiting quietly")
        return
    srv = ThreadingHTTPServer((BIND, PORT), Handler)
    log.info("SKY HUB online: http://%s:%d (brain=%s model=%s)",
             BIND, PORT, api_health()["brain"], api_health()["model"])
    srv.serve_forever()


if __name__ == "__main__":
    main()
