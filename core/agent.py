"""P3 hands: agentic tool loop via native function-calling + safety gates.

Safety model:
- HARD_BLOCK regexes -> tool refuses, model sees why (never executed)
- read-only ALLOW prefixes -> run silently
- anything else -> confirm_fn(cmd) (REPL asks the user; one-shot auto-denies)
- every executed action is logged to data/os_actions.log
"""
import ipaddress
import json
import logging
import os
import re
import shlex
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from . import llm, spy_bridge, telemetry
from .learner import web_search as _learner_web_search, read_page as _learner_read_page

log = logging.getLogger("sky.agent")

_ROOT = Path(__file__).resolve().parent.parent

# ---- safety ----
_HARD_BLOCK = [
    (r"\bformat\s+[a-zA-Z]:", "drive format"),
    (r"\bdel\s+[^;|&]*/s\b[^;|&]*[a-zA-Z]:\\", "recursive delete of a drive root"),
    (r"\brd\s+/s\b|\brmdir\s+/s\b", "recursive directory delete"),
    (r"remove-item\b[^;|&]*-recurse[^;|&]*-force", "recursive force delete"),
    (r"\bmkfs\b", "filesystem format"),
    (r"\bdd\b[^;|&]*\bof=", "raw disk write (dd of=)"),
    (r":\(\)\s*\{.*\};\s*:", "fork bomb"),
    (r"\b(shutdown|restart-computer|stop-computer)\b", "power control"),
    (r"\bbcdedit\b|\bdiskpart\b|\bcipher\s+/w\b|\bvssadmin\b", "system-level disk tooling"),
    (r"reg\s+(add|delete)\s+HKLM", "machine registry hive modification"),
    (r">\s*/dev/(sd|nvme|hd)", "raw block-device write"),
    (r"\b(curl|wget|iwr|invoke-webrequest)\b[^;|]*\|\s*(iex|invoke-expression|sh|bash)\b",
     "pipe-to-shell from the internet"),
]
_CONFIRM = [r"\bdel\b", r"\brm\b", r"\bmv\b", r"\bkill\b", r"\btaskkill\b",
            r"\bstop-process\b", r"\bstop-service\b", r"\breg\b", r"\bschtasks\b",
            r"\bpip3?\b", r"\bset-service\b", r"\bicacls\b", r"\btakeown\b",
            r"\btruncate\b", r"\bnet\s+user\b"]
_ALLOW = {"dir", "type", "whoami", "hostname", "date", "get-date", "echo",
          "get-process", "get-service", "ipconfig", "systeminfo", "tasklist",
          "get-psdrive", "get-volume", "get-computerinfo", "ver", "vol",
          "ls", "pwd", "df", "free", "ps", "uname", "head", "tail", "wc",
          "which", "cat", "nproc", "id", "env", "printenv", "stat", "top"}

_APP_ALIASES = {"calculator": "calc", "files": "explorer", "paint": "mspaint",
                "vscode": "code", "word": "winword", "terminal": "wt",
                "notepad": "notepad", "settings": "ms-settings:"}

_META = set('&|<>^%!"`')


def _log_action(action: str, detail: str, result: str):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{action}\t{detail}\t{str(result)[:200].replace(chr(10), ' ')}"
    try:
        with open(_ROOT / "data" / "os_actions.log", "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ---- tools (each returns a string for the model) ----

def t_run_shell(cmd: str, confirm_fn=None) -> str:
    cmd = cmd.strip()
    if not cmd or len(cmd) > 500:
        return "ERROR: empty or oversized command"
    for pat, why in _HARD_BLOCK:
        if re.search(pat, cmd, re.IGNORECASE):
            _log_action("shell", cmd, f"REFUSED {why}")
            return f"REFUSED: {why} — this is a hard safety rule, do not retry it."
    try:
        first = shlex.split(cmd)[0]
    except ValueError:
        first = cmd.split()[0]
    allowed = os.path.basename(first).lower() in _ALLOW
    if not allowed:
        if confirm_fn is None:
            _log_action("shell", cmd, "DENIED (non-interactive)")
            return ("NEEDS_CONFIRM: this command is not read-only. In one-shot mode it is "
                    "denied automatically. Tell the user to run it in the interactive REPL, "
                    "or use a read-only alternative.")
        if not confirm_fn(cmd):
            _log_action("shell", cmd, "DENIED by user")
            return "DENIED_BY_USER: the user said no. Do not retry it; offer an alternative."
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                           timeout=30)
        out = (r.stdout or "").strip()[:3000]
        err = (r.stderr or "").strip()[:1000]
        _log_action("shell", cmd, f"exit={r.returncode}")
        return f"exit={r.returncode}\nSTDOUT:\n{out}\nSTDERR:\n{err}" if (err := r.stderr) \
            else f"exit={r.returncode}\n{out}"
    except subprocess.TimeoutExpired:
        _log_action("shell", cmd, "TIMEOUT")
        return "ERROR: command timed out after 30s"


def t_read_file(path: str) -> str:
    p = Path(path).expanduser()
    try:
        if not p.is_file():
            return f"ERROR: not a file: {p}"
        if p.stat().st_size > 1024 * 512:
            return "ERROR: file too large to read"
        text = p.read_text(errors="replace")[:8000]
        _log_action("read_file", str(p), "ok")
        return text or "(empty file)"
    except Exception as e:
        return f"ERROR: {e}"


def t_list_dir(path: str = ".") -> str:
    p = Path(path).expanduser()
    try:
        entries = sorted(p.iterdir(), key=lambda x: x.is_dir())[:100]
        _log_action("list_dir", str(p), "ok")
        return "\n".join(("d " if e.is_dir() else "f ") + e.name for e in entries) or "(empty)"
    except Exception as e:
        return f"ERROR: {e}"


def t_launch_app(target: str) -> str:
    target = (target or "").strip()
    if not target or len(target) > 200:
        return "ERROR: empty target"
    target = _APP_ALIASES.get(target.lower().rstrip(":"), target)
    low = target.lower()
    ok = (low.startswith(("http://", "https://", "mailto:", "ms-settings:", "file:///"))
          or re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}(/\S*)?", low)
          or re.fullmatch(r"[a-zA-Z0-9_\- ]+(\.[a-zA-Z0-9]+)?", target))
    if not ok or any(c in target for c in _META):
        return f"ERROR: refused target {target!r}"
    try:
        os.startfile(target)  # native Windows shell open (ShellExecute)
        _log_action("launch", target, "ok")
        return f"launch requested for {target!r} (ShellExecute; not verified visible)."
    except OSError as e:
        _log_action("launch", target, f"FAIL {e}")
        return f"ERROR: {e}"


def t_sys_report() -> str:
    return telemetry.report()


def t_web_fetch(url: str) -> str:
    try:
        u = urllib.parse.urlparse(url)
        if u.scheme not in ("http", "https") or not u.hostname:
            return "ERROR: only http(s) URLs allowed"
        ip = socket.gethostbyname(u.hostname)
        if ipaddress.ip_address(ip).is_private or ipaddress.ip_address(ip).is_loopback:
            return "ERROR: private addresses are blocked"
        req = urllib.request.Request(url, headers={"User-Agent": "sky/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read(200_000).decode("utf-8", "replace")
        text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", raw,
                      flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        _log_action("web_fetch", url, "ok")
        return text[:3000] or "(empty page)"
    except Exception as e:
        return f"ERROR: {e}"


def t_set_reminder(text: str, at: str, mem) -> str:
    due = _parse_time(at)
    if due is None:
        return ("ERROR: cannot parse time. Use HH:MM, 'in 10m', 'in 2h', "
                "or 'YYYY-MM-DD HH:MM'.")
    mem.add_reminder(text[:200], due)
    _log_action("reminder", f"{text[:50]} @ {at}", "ok")
    return f"Reminder set: {time.strftime('%Y-%m-%d %H:%M', time.localtime(due))} — {text[:80]}"


def _parse_time(s: str):
    s = s.strip().lower()
    now = time.time()
    m = re.fullmatch(r"in\s+(\d+)\s*(m|min|mins|h|hr|hrs)", s)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        return now + n * 60 * (60 if unit.startswith("h") else 1)
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", s)
    if m:
        import datetime
        t = time.localtime()
        due = time.mktime((t.tm_year, t.tm_mon, t.tm_mday,
                           int(m.group(1)), int(m.group(2)), 0, 0, 0, -1))
        return due if due > now else due + 86400
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})[ T](\d{1,2}):(\d{2})", s)
    if m:
        import calendar
        y, mo, d = map(int, m.group(1).split("-"))
        return time.mktime((y, mo, d, int(m.group(2)), int(m.group(3)), 0, 0, 0, -1))
    return None


# ---- Hologram control (hologram_app.py at SKY root, port 20129) ----

_HOLO_APP = _ROOT / "hologram_app.py"


def _holo_post(path: str, form: dict | None = None, timeout: float = 2.0):
    req = urllib.request.Request(
        f"http://127.0.0.1:20129{path}",
        data=urllib.parse.urlencode(form or {}).encode(), method="POST")
    return urllib.request.urlopen(req, timeout=timeout).read(100).decode()


def _holo_ping(timeout: float = 0.5) -> bool:
    try:
        urllib.request.urlopen("http://127.0.0.1:20129/ping",
                               timeout=timeout).close()
        return True
    except Exception:
        return False


def t_toggle_hologram(on=None) -> str:
    """Turn the animated hologram head on/off. Flips config.json's
    `hologram` flag and starts/stops hologram_app.py to match."""
    import json as _json
    try:
        cfg = _json.loads((_ROOT / "config.json").read_text())
    except Exception as e:
        return f"ERROR: cannot read config.json: {e}"
    cur = bool(cfg.get("hologram"))
    target = (not cur) if on is None else bool(on)
    if target:
        if not _HOLO_APP.is_file():
            return "ERROR: hologram_app.py not found at SKY root."
        if not _holo_ping():
            flags = 0
            if os.name == "nt":
                flags = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                         | getattr(subprocess, "DETACHED_PROCESS", 0))
            try:
                subprocess.Popen([sys.executable, str(_HOLO_APP)], cwd=str(_ROOT),
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL,
                                 creationflags=flags)
            except Exception as e:
                return f"ERROR: could not start hologram app: {e}"
            for _ in range(20):  # up to ~4s for the control port
                if _holo_ping():
                    break
                time.sleep(0.2)
        if not _holo_ping():
            return ("ERROR: hologram app did not come up on port 20129 "
                    "(check data/ or run it manually to see the error).")
        _holo_post("/show")
    elif _holo_ping():
        try:
            _holo_post("/quit")
        except Exception:
            pass
    if target == cur and target:
        return "Hologram is already on, sir — the head is showing."
    cfg["hologram"] = target
    try:
        (_ROOT / "config.json").write_text(_json.dumps(cfg, indent=2) + "\n")
    except Exception as e:
        return f"ERROR: cannot write config.json: {e}"
    return ("Hologram ON — the head is on screen and every line I speak "
            "moves its mouth." if target else "Hologram OFF.")


# ---- Spy skill tools (via spy_bridge -> Windows modules) ----

def _spy(name: str, fn: str, timeout: int = 60, **kwargs) -> str:
    """Call a Spy Windows module; returns a compact string for the model."""
    if not spy_bridge.available():
        return "ERROR: Spy bridge unavailable (Windows python not reachable)."
    try:
        res = spy_bridge.call(name, fn, timeout=timeout, **kwargs)
    except Exception as e:
        _log_action(f"spy:{name}.{fn}", str(kwargs)[:120], f"FAIL {str(e)[:80]}")
        return f"ERROR: {e}"
    out = json.dumps(res, default=str) if not isinstance(res, str) else res
    _log_action(f"spy:{name}.{fn}", str(kwargs)[:120], "ok")
    return out[:4000] or "(no output)"


def _win_path(path: str) -> str:
    """Map any path to a native Windows form (SKY runs on Windows).

    C:\\... / C:/...      -> C:\\...
    /mnt/c/... (WSL form) -> C:\\...
    anything else         -> unchanged (relative paths resolve from cwd)
    """
    s = path.replace("\\", "/")
    if s[1:2] == ":":  # already a Windows path — keep it native
        return s[:2].upper() + s[2:].replace("/", "\\")
    if s.startswith("/mnt/") and len(s) > 6 and s[5].isalpha():
        drive = s[5].upper()
        return f"{drive}:\\" + s[7:].replace("/", "\\")
    return path


def t_spy_analyze_data(path: str) -> str:
    return _spy("spy_data", "analyse", timeout=120, source=_win_path(path))


def t_spy_simulate(kind: str, **params) -> str:
    return _spy("spy_data", "simulate", kind=kind, **params)


def t_spy_security_scan() -> str:
    return _spy("spy_sec", "security_scan", timeout=120)


def t_spy_scan_network(cidr: str = "") -> str:
    return _spy("spy_sec", "scan_network", timeout=120, cidr=cidr)


def t_spy_hash_file(path: str, algorithm: str = "sha256") -> str:
    return _spy("spy_sec", "hash_file", path=path, algorithms=(algorithm,))


def t_spy_encrypt_file(path: str, password: str, decrypt: bool = False) -> str:
    fn = "decrypt_file" if decrypt else "encrypt_file"
    return _spy("spy_sec", fn, timeout=120, path=path, password=password)


def t_spy_password_strength(password: str) -> str:
    return _spy("spy_sec", "password_strength", pw=password)


def t_spy_send_email(to: str, subject: str, body: str) -> str:
    return _spy("spy_ops", "send_email", timeout=90, to=to, subject=subject, body=body)


def t_spy_organize_folder(path: str, apply: bool = False) -> str:
    return _spy("spy_ops", "organize_folder", timeout=120, path=path, apply=bool(apply))


def t_spy_smart_home(device: str = "", action: str = "", value=None) -> str:
    return _spy("spy_ops", "smart_home", timeout=30,
                device=device, action=action, value=value)


def t_web_search(query: str) -> str:
    res = _learner_web_search(query)
    return res.get("message", str(res))


def t_read_page(url: str) -> str:
    res = _learner_read_page(url)
    return res.get("message", str(res))


# ---- Spy-skill tools (Windows-native, confirm-gated where they act) ----

def t_type_text(text: str, confirm_fn=None) -> str:
    if confirm_fn and not confirm_fn("type text on the keyboard", text[:80]):
        return "REFUSED: user declined."
    return _spy("automation", "type_text", timeout=60, text=text)


def t_click_at(x: int, y: int, button: str = "left", double: bool = False,
               confirm_fn=None) -> str:
    where = f"click {button}{' double' if double else ''} at ({x}, {y})"
    if confirm_fn and not confirm_fn(where, ""):
        return "REFUSED: user declined."
    return _spy("automation", "click_at", timeout=60,
                x=int(x), y=int(y), button=button, double=bool(double))


def t_press_key(key: str, confirm_fn=None) -> str:
    if confirm_fn and not confirm_fn(f"press key {key!r}", ""):
        return "REFUSED: user declined."
    return _spy("automation", "press_key", timeout=60, key=key)


def t_get_screen_info() -> str:
    info = _spy("automation", "screen_size")
    win = _spy("automation", "active_window_title")
    return f"screen: {info} | active window: {win}"


def t_take_screenshot(confirm_fn=None) -> str:
    if confirm_fn and not confirm_fn("take a screenshot", ""):
        return "REFUSED: user declined."
    return _spy("automation", "take_screenshot", timeout=60)


def t_run_python(code: str, confirm_fn=None) -> str:
    """Sandboxed python: fresh interpreter (-I), blocking imports denied,
    no site packages, 30 s timeout, capped output. Confirm-gated."""
    if confirm_fn and not confirm_fn("run this Python code", code[:120]):
        return "REFUSED: user declined."
    blocked = ("os sys subprocess socket shutil ctypes importlib urllib http "
               "requests multiprocessing threading pickle builtins pathlib "
               "glob tempfile open builtins_open eval exec compile __import__")
    # The child program: install an import blocker, then exec the real code
    # (passed after a marker on stdin) with open/eval/exec/compile neutered.
    guard = (
        "import sys, io\n"
        "BLOCKED = set('''" + blocked + "'''.split())\n"
        "# os and friends are pre-imported at startup — evict them so a new\n"
        "# `import os` has to go through meta_path and hit the blocker\n"
        "for _n in [n for n in sys.modules if n.split('.')[0] in BLOCKED]:\n"
        "    del sys.modules[_n]\n"
        "class _B:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name.split('.')[0] in BLOCKED:\n"
        "            raise ImportError(name + ' is not allowed in the sandbox')\n"
        "sys.meta_path.insert(0, _B())\n"
        "def _no_open(*a, **k):\n"
        "    raise PermissionError('file access is not allowed in the sandbox')\n"
        "_src = sys.stdin.read()\n"
        "_marker, _, _code = _src.partition('#__CODE__\\n')\n"
        "_g = {'__name__': 'sandbox', 'print': print, 'range': range,\n"
        "      'len': len, 'str': str, 'int': int, 'float': float,\n"
        "      'list': list, 'dict': dict, 'set': set, 'sum': sum,\n"
        "      'abs': abs, 'min': min, 'max': max, 'sorted': sorted,\n"
        "      'enumerate': enumerate, 'zip': zip, 'map': map,\n"
        "      'round': round, 'open': _no_open}\n"
        "try:\n"
        "    exec(compile(_code, '<sandbox>', 'exec'), _g)\n"
        "except SystemExit:\n"
        "    pass\n"
    )
    payload = "#__CODE__\n" + code
    try:
        r = subprocess.run(
            [sys.executable, "-I", "-c", guard],
            input=payload, capture_output=True, text=True, timeout=30,
            env={"PATH": "", "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
                 "TMPDIR": tempfile.gettempdir()},
        )
    except subprocess.TimeoutExpired:
        return "ERROR: code timed out after 30 s."
    out = (r.stdout or "")[:4000]
    err = (r.stderr or "")[-1000:]
    if r.returncode != 0:
        return f"ERROR (exit {r.returncode}):\n{err}\nstdout:\n{out}"
    return out or "(no output; exit 0)"


def t_run_diagnostics() -> str:
    return _spy("spy_vitals", "diagnose", timeout=120)


def t_vitals() -> str:
    return _spy("spy_vitals", "get_vitals", timeout=60)


def t_check_knowledge(query: str, mem) -> str:
    """Search SKY's own knowledge: learned facts + identity + chat log."""
    query = (query or "").strip()
    if not query:
        return "ERROR: empty query"
    hits, seen = [], set()
    with mem.lock:
        for w in re.findall(r"\w{3,}", query)[:6] or [query]:
            like = f"%{w}%"
            for kind, key, val in mem.conn.execute(
                    "SELECT kind, key, value FROM facts WHERE value LIKE ? "
                    "ORDER BY id DESC LIMIT 6", (like,)):
                line = f"[{kind}{':' + key if key else ''}] {val[:220]}"
                if line not in seen:
                    seen.add(line)
                    hits.append(line)
    if not hits:
        learned = mem.conn.execute(
            "SELECT value FROM facts WHERE kind='general' AND value LIKE 'learned: %' "
            "ORDER BY id DESC LIMIT 5").fetchall()
        if learned:
            hits = [v[0][:220] for v in learned]
            return "No direct match — newest learned facts:\n" + "\n".join(hits)
        return "No knowledge stored yet — try web_search first."
    return "Knowledge:\n" + "\n".join(hits[:10])


_ENCYCLOPEDIA = Path(__file__).resolve().parent.parent / "knowledge.txt"
_ENCY_STOP = {"the", "and", "for", "what", "how", "why", "about", "explain",
              "tell", "does", "did", "with", "that", "this", "from", "are",
              "was", "were", "kya", "hai", "batao", "kaise", "mein", "have",
              "can", "you", "all", "things", "deep", "deeply", "detail"}


def t_deep_knowledge(query: str) -> str:
    """Search the deep encyclopedia (knowledge.txt Part II) for a topic."""
    query = (query or "").strip()
    if not query:
        return "ERROR: empty query"
    try:
        text = _ENCYCLOPEDIA.read_text(encoding="utf-8")
    except OSError:
        return "ERROR: encyclopedia file missing"
    sections, title, body = [], None, []
    for ln in text.splitlines():
        if ln.startswith("### "):
            if title:
                sections.append((title, "\n".join(body)))
            title, body = ln[4:].strip(), []
        elif title:
            body.append(ln)
    if title:
        sections.append((title, "\n".join(body)))
    if not sections:
        return "ERROR: encyclopedia has no sections"
    words = [w.lower() for w in re.findall(r"[a-zA-Z]{3,}", query)
             if w.lower() not in _ENCY_STOP]
    words = [w[:-1] if w.endswith("s") and len(w) > 4 else w for w in words]
    if not words:
        words = [w.lower() for w in re.findall(r"[a-zA-Z]{3,}", query)]

    def _hits(low: str, w: str) -> int:
        return len(re.findall(r"\b" + re.escape(w), low))

    # IDF: words common across many sections (e.g. "first") count less,
    # rare topical words (e.g. "burn") count more.
    n_sec = max(len(sections), 1)
    doc_freq = {}
    for stitle, sbody in sections:
        low = (stitle + " " + sbody).lower()
        seen = {w for w in words if re.search(r"\b" + re.escape(w), low)}
        for w in seen:
            doc_freq[w] = doc_freq.get(w, 0) + 1
    scored = []
    for stitle, sbody in sections:
        t_low, b_low = stitle.lower(), sbody.lower()
        score = 0.0
        for w in words:
            idf = n_sec / (1 + max(doc_freq.get(w, 0), 1))
            if re.search(r"\b" + re.escape(w), t_low):
                score += 5 * idf
            score += min(_hits(b_low, w), 6) * idf
        if score > 0.5:
            scored.append((score, stitle, sbody))
    if not scored:
        return "No section matched in the encyclopedia — use web_search."
    scored.sort(key=lambda x: -x[0])
    out, total = [], 0
    for _, stitle, sbody in scored[:2]:
        chunk = f"### {stitle}\n{sbody.strip()[:2600]}"
        out.append(chunk)
        total += len(chunk)
        if total >= 4200:
            break
    return "\n\n".join(out)


def t_list_reminders(mem) -> str:
    rows = mem.pending_reminders()
    if not rows:
        return "No pending reminders."
    out = ["Pending reminders:"]
    for rid, text, due in rows[:15]:
        when = datetime.fromtimestamp(due).strftime("%d %b %H:%M")
        out.append(f"  #{rid}  {when}  {text[:80]}")
    return "\n".join(out)


def t_delete_reminder(reminder_id: int, mem) -> str:
    ok = mem.delete_reminder(int(reminder_id))
    return (f"Reminder #{reminder_id} cancelled." if ok
            else f"No pending reminder #{reminder_id} (already fired or wrong id).")


# ---- Sky-native memory tools ----

def t_remember_fact(text: str, mem) -> str:
    text = (text or "").strip()
    if not text:
        return "ERROR: empty fact"
    mem.add_general(text[:200])
    _log_action("remember", text[:60], "ok")
    return f"Stored permanently: {text[:100]}"


def t_recall_memory(query: str, mem) -> str:
    query = (query or "").strip()
    if not query:
        return "ERROR: empty query"
    import re as _re
    words = _re.findall(r"\w{3,}", query)[:6] or [query]
    hits, seen = [], set()
    with mem.lock:
        for w in words:
            like = f"%{w}%"
            for kind, key, val in mem.conn.execute(
                    "SELECT kind, key, value FROM facts WHERE value LIKE ? "
                    "ORDER BY id DESC LIMIT 8", (like,)):
                tag = f"{kind}:{key}" if key else kind
                if val not in seen:
                    seen.add(val)
                    hits.append(f"[{tag}] {val[:200]}")
            for role, content in mem.conn.execute(
                    "SELECT role, content FROM messages WHERE content LIKE ? "
                    "ORDER BY id DESC LIMIT 5", (like,)):
                who = "you" if role == "user" else "sky"
                line = f"[chat {who}] {content[:180]}"
                if line not in seen:
                    seen.add(line)
                    hits.append(line)
            if len(hits) >= 15:
                break
    _log_action("recall", query[:60], f"{len(hits)} hits")
    if not hits:
        return f"Nothing in memory matches {query!r}."
    return "Found:\n" + "\n".join(hits[:15])


# ---- GitHub addon tools (repos installed under SKY\repos) ----

_SKILL_REPOS = ("scientific-agent-skills", "Anthropic-Cybersecurity-Skills",
                "diagram-design", "awesome-harness-engineering",
                "OpenViking", "agentmemory")

_ADDON_ENV_PY = _ROOT / "addons-env" / "Scripts" / "python.exe"
_BROWSER_TASK_PY = _ROOT / "addons" / "browser_task.py"
_AGENTMEMORY_URL = "http://127.0.0.1:3111"


def t_skills_search(query: str) -> str:
    """Search the installed GitHub skill libraries (165 scientific skills,
    817 cybersecurity skills, diagram design, harness engineering, docs)."""
    query = (query or "").strip()
    if not query:
        return "ERROR: empty query"
    repos_root = _ROOT / "repos"
    if not repos_root.is_dir():
        return "ERROR: no repos folder installed"
    terms = [t for t in re.split(r"[^a-z0-9+#._-]+", query.lower()) if t]
    if not terms:
        return "ERROR: empty query"
    need = max(1, len(terms) // 2)
    hits, scanned = [], 0
    for repo in _SKILL_REPOS:
        rdir = repos_root / repo
        if not rdir.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(rdir):
            dirnames[:] = [d for d in dirnames
                           if d not in (".git", "node_modules", "__pycache__",
                                        "assets", "website", "test", "tests")]
            for fn in filenames:
                if not fn.lower().endswith((".md", ".txt")):
                    continue
                fp = Path(dirpath) / fn
                try:
                    if fp.stat().st_size > 300_000:
                        continue
                    text = fp.read_text(encoding="utf-8", errors="ignore")[:60_000]
                except OSError:
                    continue
                low = text.lower()
                score = sum(1 for t in terms if t in low)
                if score >= need:
                    idx = low.find(terms[0])
                    if idx < 0:
                        idx = 0
                    snip = text[max(0, idx - 60): idx + 200].replace("\n", " ")
                    hits.append((score, f"{repo}/{fp.relative_to(rdir).as_posix()}", snip))
    _log_action("skills_search", query[:60], f"{len(hits)} hits")
    if not hits:
        return f"No skill in the installed libraries matches {query!r}."
    hits.sort(key=lambda h: -h[0])
    out = []
    for score, path, snip in hits[:8]:
        out.append(f"[{path}]\n  ...{snip.strip()[:260]}...")
    return ("Top matches (use read_file on the path for the full skill):\n"
            + "\n".join(out))


def t_browser_task(task: str, confirm_fn=None) -> str:
    """Run a browser automation task with browser-use (real Chromium)."""
    task = (task or "").strip()
    if not task or len(task) > 2000:
        return "ERROR: empty or oversized task"
    if confirm_fn is not None and not confirm_fn(f"let me control a browser to: {task[:100]}"):
        _log_action("browser_task", task[:60], "DENIED by user")
        return "DENIED_BY_USER: the user said no. Do not retry; offer an alternative."
    py = _ADDON_ENV_PY
    if not _ADDON_ENV_PY.is_file():
        return "ERROR: addons-env not installed (browser-use missing)"
    try:
        r = subprocess.run(
            [str(_ADDON_ENV_PY), str(_BROWSER_TASK_PY),
             json.dumps({"task": task, "headless": True, "max_steps": 12})],
            capture_output=True, text=True, timeout=300,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = (r.stdout or "").strip()
        try:
            marker = out.rfind("###SKY_BT###")
            if marker < 0:
                raise ValueError("no result marker in output")
            data = json.loads(out[marker + len("###SKY_BT###"):])
        except Exception:
            err = (r.stderr or out or "no output")[-400:]
            return f"ERROR: browser task failed: {err}"
        _log_action("browser_task", task[:60], "ok" if data.get("ok") else "fail")
        if not data.get("ok"):
            return f"ERROR: {data.get('error', 'unknown browser error')}"
        steps = data.get("steps", "?")
        return f"Browser task done ({steps} steps). Result: {data.get('result', '')}"
    except subprocess.TimeoutExpired:
        return "ERROR: browser task timed out (5 min limit)"
    except Exception as e:
        return f"ERROR: {e}"


def _agentmemory_up() -> bool:
    try:
        with urllib.request.urlopen(_AGENTMEMORY_URL + "/agentmemory/livez",
                                    timeout=1.5) as resp:
            return resp.status == 200
    except Exception:
        return False


def t_agent_memory_save(text: str, mem) -> str:
    """Save a fact/observation. Uses the agentmemory server when it is
    running; otherwise falls back to Sky's own sqlite memory."""
    text = (text or "").strip()
    if not text:
        return "ERROR: empty memory"
    try:
        body = json.dumps({"content": text[:2000], "project": "sky",
                           "type": "fact"}).encode()
        req = urllib.request.Request(_AGENTMEMORY_URL + "/agentmemory/remember",
                                     data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            if resp.status in (200, 201):
                _log_action("am_save", text[:60], "server")
                return f"Saved to agentmemory: {text[:100]}"
    except Exception:
        pass
    mem.add_general(text[:200])
    _log_action("am_save_fallback", text[:60], "sqlite")
    return f"Saved (Sky memory; agentmemory server not running): {text[:100]}"


def t_agent_memory_search(query: str, mem) -> str:
    query = (query or "").strip()
    if not query:
        return "ERROR: empty query"
    try:
        body = json.dumps({"query": query, "limit": 5}).encode()
        req = urllib.request.Request(_AGENTMEMORY_URL + "/agentmemory/search",
                                     data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
        items = data.get("results") or data.get("memories") or data
        if isinstance(items, list) and items:
            lines = []
            for it in items[:8]:
                if isinstance(it, dict):
                    obs = it.get("observation")
                    if isinstance(obs, dict):
                        narr = obs.get("narrative") or obs.get("title")
                        facts = obs.get("facts") or []
                        line = narr or " | ".join(str(f) for f in facts) or str(obs)
                    else:
                        line = (it.get("text") or it.get("content")
                                or it.get("narrative") or str(it))
                    lines.append(str(line)[:200])
                else:
                    lines.append(str(it)[:200])
            _log_action("am_search", query[:60], f"{len(lines)} hits")
            return "agentmemory found:\n" + "\n".join(lines)
        return f"agentmemory has nothing matching {query!r}."
    except Exception:
        pass
    return t_recall_memory(query, mem)  # graceful fallback to Sky sqlite


# ---- OpenAI tool schema ----

def _spy_tool(name: str, desc: str, props: dict, req: list) -> dict:
    return {"type": "function", "function": {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props,
                           "required": req}}}


_SPY_TOOLS = [
    _spy_tool("analyze_data",
              "Profile any CSV/TSV/JSON/Excel file: row/column counts, per-column "
              "statistics, outliers, correlations. Windows or Linux paths both work.",
              {"path": {"type": "string"}}, ["path"]),
    _spy_tool("run_simulation",
              "Run a real RK4 physics simulation. kind: projectile (v0, angle_deg, "
              "drag), pendulum (length_m, theta0_deg, damping), spring (mass_kg, "
              "k, damping), cooling (t0_c, t_env_c, k), orbit (alt_km).",
              {"kind": {"type": "string"},
               "v0": {"type": "number"}, "angle_deg": {"type": "number"},
               "drag": {"type": "number"}, "length_m": {"type": "number"},
               "theta0_deg": {"type": "number"}, "damping": {"type": "number"},
               "mass_kg": {"type": "number"}, "k": {"type": "number"},
               "t0_c": {"type": "number"}, "t_env_c": {"type": "number"},
               "alt_km": {"type": "number"}},
              ["kind"]),
    _spy_tool("security_scan",
              "Defensive audit of the user's OWN Windows machine: updates, "
              "Defender, firewall, listening ports, shares.",
              {}, []),
    _spy_tool("scan_network",
              "Map the user's OWN local network (ping sweep + ARP). Optional cidr.",
              {"cidr": {"type": "string"}}, []),
    _spy_tool("hash_file",
              "Compute a file hash (sha256/md5/sha1) to verify integrity.",
              {"path": {"type": "string"},
               "algorithm": {"type": "string"}}, ["path"]),
    _spy_tool("encrypt_file",
              "Password-encrypt or decrypt the user's OWN file (set decrypt=true "
              "to decrypt).",
              {"path": {"type": "string"}, "password": {"type": "string"},
               "decrypt": {"type": "boolean"}},
              ["path", "password"]),
    _spy_tool("password_strength",
              "Offline password strength audit. Never echo the password back.",
              {"password": {"type": "string"}}, ["password"]),
    _spy_tool("send_email",
              "Send an email from the user's own account (SMTP from spy.env). "
              "Confirm recipient and content with the user before sending.",
              {"to": {"type": "string"}, "subject": {"type": "string"},
               "body": {"type": "string"}},
              ["to", "subject", "body"]),
    _spy_tool("organize_folder",
              "Tidy a folder into category subfolders. ALWAYS dry-run first "
              "(apply=false), and only apply=true after the user agrees.",
              {"path": {"type": "string"}, "apply": {"type": "boolean"}},
              ["path"]),
    _spy_tool("smart_home",
              "Control Philips Hue lights. action: on/off/brightness/color; "
              "device empty = all lights; value for brightness (1-100) or color.",
              {"device": {"type": "string"}, "action": {"type": "string"},
               "value": {}}, ["action"]),
    _spy_tool("web_search",
              "Search the web (DuckDuckGo) for anything current — news, prices, "
              "scores, weather. Prefer this over guessing.",
              {"query": {"type": "string"}}, ["query"]),
    _spy_tool("read_page",
              "Fetch the full readable text of a web page (more depth than "
              "web_fetch).",
              {"url": {"type": "string"}}, ["url"]),
    _spy_tool("remember_fact",
              "Store a permanent fact about the user in Sky's memory.",
              {"text": {"type": "string"}}, ["text"]),
    _spy_tool("recall_memory",
              "Keyword-search permanent facts and past conversations BEFORE "
              "saying you don't remember something.",
              {"query": {"type": "string"}}, ["query"]),
]



TOOLS = [
    {"type": "function", "function": {"name": "run_shell", "description":
        "Run a shell/PowerShell command on this Windows machine. Read-only "
        "commands run freely; others need user confirmation; destructive ones "
        "are hard-blocked.",
        "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}},
                       "required": ["cmd"]}}},
    {"type": "function", "function": {"name": "read_file", "description":
        "Read a text file (first 8KB).", "parameters": {"type": "object",
        "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "list_dir", "description":
        "List a directory.", "parameters": {"type": "object",
        "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "launch_app", "description":
        "Open an app, file, or URL (calculator, vscode, youtube.com, ...).",
        "parameters": {"type": "object",
        "properties": {"target": {"type": "string"}}, "required": ["target"]}}},
    {"type": "function", "function": {"name": "sys_report", "description":
        "Live CPU/RAM/disk/top-processes report. Use for 'what's slow' questions.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "web_fetch", "description":
        "Fetch a public web page as plain text (read-only GET).",
        "parameters": {"type": "object", "properties": {"url": {"type": "string"}},
                       "required": ["url"]}}},
    {"type": "function", "function": {"name": "set_reminder", "description":
        "Set a reminder. `at` = HH:MM today, 'in 10m', 'in 2h', or 'YYYY-MM-DD HH:MM'. "
        "CALL IMMEDIATELY when the user's message has a time plus anything to "
        "remember — even vague, even Hinglish ('2 minut bad yaad dilao ki ...'). "
        "Use the user's own words; NEVER ask clarifying questions first.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string"}, "at": {"type": "string"}},
            "required": ["text", "at"]}}},
    {"type": "function", "function": {"name": "list_reminders", "description":
        "List pending (not yet fired) reminders with their ids.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "delete_reminder", "description":
        "Cancel a pending reminder by its id (get ids from list_reminders).",
        "parameters": {"type": "object", "properties": {
            "reminder_id": {"type": "integer"}}, "required": ["reminder_id"]}}},
    {"type": "function", "function": {"name": "type_text", "description":
        "Type text into the currently focused window, as if on the keyboard. "
        "Confirm-gated: the user is asked first.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string"}}, "required": ["text"]}}},
    {"type": "function", "function": {"name": "click_at", "description":
        "Move the mouse and click at screen coordinates (x, y). Confirm-gated.",
        "parameters": {"type": "object", "properties": {
            "x": {"type": "integer"}, "y": {"type": "integer"},
            "button": {"type": "string"}, "double": {"type": "boolean"}},
            "required": ["x", "y"]}}},
    {"type": "function", "function": {"name": "press_key", "description":
        "Press a key or combo in the focused window (e.g. 'enter', 'ctrl+s', "
        "'alt+tab'). Confirm-gated.",
        "parameters": {"type": "object", "properties": {
            "key": {"type": "string"}}, "required": ["key"]}}},
    {"type": "function", "function": {"name": "get_screen_info", "description":
        "Screen resolution + title of the currently active window. Read-only.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "take_screenshot", "description":
        "Capture the screen to a PNG in data/screenshots and return the path. "
        "Confirm-gated.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "run_python", "description":
        "Run a short Python snippet in a locked-down sandbox: no file, network, "
        "or subprocess access, 30 s limit. For math and quick computation. "
        "Confirm-gated. Use run_simulation for physics instead.",
        "parameters": {"type": "object", "properties": {
            "code": {"type": "string"}}, "required": ["code"]}}},
    {"type": "function", "function": {"name": "run_diagnostics", "description":
        "Full system sweep: battery, RAM, disk, internet, CPU, defender, "
        "firmware. For 'run diagnostics' / 'check my system'.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "vitals", "description":
        "Quick vitals snapshot (battery, RAM, disk, internet, CPU, active "
        "window, top RAM-hogging processes) as JSON. Use for 'what's eating "
        "my RAM'.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "check_knowledge", "description":
        "Search SKY's own learned knowledge (from background learning and "
        "past browsing) before saying you don't know something current.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "deep_knowledge", "description":
        "Search SKY's built-in deep encyclopedia (science, math, space, human "
        "body, computing, history, India, geography, money, law, languages, "
        "philosophy, arts, psychology, sports, practical life). Use English "
        "keywords. Call BEFORE answering deep how/why questions; use "
        "web_search for anything current.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "toggle_hologram", "description":
        "Show or hide the animated SKY hologram head (bottom-right of the "
        "desktop). on=true to show, on=false to hide, omit `on` to flip.",
        "parameters": {"type": "object", "properties": {
            "on": {"type": "boolean"}}}}},
    {"type": "function", "function": {"name": "skills_search", "description":
        "Search 1000+ installed expert skill libraries (GitHub): 165 scientific "
        "research skills, 817 cybersecurity skills (MITRE ATT&CK, NIST), "
        "diagram-design (architecture/flow diagrams), agent harness engineering "
        "patterns. Use for 'how to', research methods, security procedures, "
        "diagram requests. Returns matching skill files to read.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "browser_task", "description":
        "Control a real Chromium browser with AI to complete a web task end-to-end: "
        "open sites, click, fill forms, read pages, return the result. Use when "
        "web_fetch/web_search is not enough (logins, multi-step sites, dashboards). "
        "Example task: 'go to weather.com and get today's temperature in Delhi'. "
        "Confirm-gated.",
        "parameters": {"type": "object", "properties": {
            "task": {"type": "string"}}, "required": ["task"]}}},
    {"type": "function", "function": {"name": "agent_memory_save", "description":
        "Store an important fact/observation in the long-term agent memory "
        "database (agentmemory server when running, Sky sqlite otherwise).",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string"}}, "required": ["text"]}}},
    {"type": "function", "function": {"name": "agent_memory_search", "description":
        "Semantic/keyword search over the long-term agent memory store. Call "
        "BEFORE saying you don't remember; complements recall_memory.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}}, "required": ["query"]}}},
] + _SPY_TOOLS


def _execute(name: str, args: dict, mem, confirm_fn) -> str:
    try:
        if name == "run_shell":
            return t_run_shell(args.get("cmd", ""), confirm_fn)
        if name == "read_file":
            return t_read_file(args.get("path", ""))
        if name == "list_dir":
            return t_list_dir_safe(args)
        if name == "launch_app":
            return t_launch_app_safe(args)
        if name == "sys_report":
            return t_sys_report()
        if name == "web_fetch":
            return t_web_fetch_safe(args)
        if name == "set_reminder":
            return t_set_reminder(args.get("text", ""), args.get("at", ""), mem)
        if name == "list_reminders":
            return t_list_reminders(mem)
        if name == "delete_reminder":
            return t_delete_reminder(args.get("reminder_id", 0), mem)
        if name == "type_text":
            return t_type_text(args.get("text", ""), confirm_fn)
        if name == "click_at":
            return t_click_at(args.get("x", 0), args.get("y", 0),
                              args.get("button", "left"),
                              bool(args.get("double", False)), confirm_fn)
        if name == "press_key":
            return t_press_key(args.get("key", ""), confirm_fn)
        if name == "get_screen_info":
            return t_get_screen_info()
        if name == "take_screenshot":
            return t_take_screenshot(confirm_fn)
        if name == "run_python":
            return t_run_python(args.get("code", ""), confirm_fn)
        if name == "run_diagnostics":
            return t_run_diagnostics()
        if name == "vitals":
            return t_vitals()
        if name == "check_knowledge":
            return t_check_knowledge(args.get("query", ""), mem)
        if name == "deep_knowledge":
            return t_deep_knowledge(args.get("query", ""))
        if name == "toggle_hologram":
            return t_toggle_hologram(args.get("on"))
        if name == "analyze_data":
            return t_spy_analyze_data(args.get("path", ""))
        if name == "run_simulation":
            params = {k: v for k, v in args.items() if k != "kind"}
            return t_spy_simulate(args.get("kind", "projectile"), **params)
        if name == "security_scan":
            return t_spy_security_scan()
        if name == "scan_network":
            return t_spy_scan_network(args.get("cidr", ""))
        if name == "hash_file":
            return t_spy_hash_file(args.get("path", ""), args.get("algorithm", "sha256"))
        if name == "encrypt_file":
            return t_spy_encrypt_file(args.get("path", ""), args.get("password", ""),
                                      bool(args.get("decrypt", False)))
        if name == "password_strength":
            return t_spy_password_strength(args.get("password", ""))
        if name == "send_email":
            return t_spy_send_email(args.get("to", ""), args.get("subject", ""),
                                    args.get("body", ""))
        if name == "organize_folder":
            return t_spy_organize_folder(args.get("path", ""), bool(args.get("apply", False)))
        if name == "smart_home":
            return t_spy_smart_home(args.get("device", ""), args.get("action", ""),
                                    args.get("value"))
        if name == "web_search":
            return t_web_search(args.get("query", ""))
        if name == "read_page":
            return t_read_page(args.get("url", ""))
        if name == "remember_fact":
            return t_remember_fact(args.get("text", ""), mem)
        if name == "recall_memory":
            return t_recall_memory(args.get("query", ""), mem)
        if name == "skills_search":
            return t_skills_search(args.get("query", ""))
        if name == "browser_task":
            return t_browser_task(args.get("task", ""), confirm_fn)
        if name == "agent_memory_save":
            return t_agent_memory_save(args.get("text", ""), mem)
        if name == "agent_memory_search":
            return t_agent_memory_search(args.get("query", ""), mem)
        return f"ERROR: unknown tool {name}"
    except Exception as e:
        log.exception("tool %s crashed", name)
        return f"ERROR: {e}"


def t_list_dir_safe(args):
    return t_list_dir(args.get("path", "."))


def t_launch_app_safe(args):
    return t_launch_app(args.get("target", ""))


def t_web_fetch_safe(args):
    return t_web_fetch(args.get("url", ""))


AGENT_PROMPT = (
    "You are SKY with HANDS, running natively on the user's Windows PC. Use "
    "the tools to actually DO things instead of saying you cannot. Rules: for "
    "'what's slow' use sys_report; for 'run diagnostics' use run_diagnostics; "
    "for battery/RAM checks or 'what's eating my RAM' use vitals; for opening "
    "apps/sites use "
    "launch_app; to type/click/press keys use type_text, click_at, press_key "
    "(the user is asked to confirm each); for anything current use web_search "
    "then read_page for depth; for deep how/why questions (science, history, "
    "body, tech) call deep_knowledge FIRST, then answer in your own voice; "
    "for notes and alarms use set_reminder "
    "(list_reminders/delete_reminder to manage them); for deep expert skills "
    "(research methods, cybersecurity procedures, diagrams, agent building) "
    "call skills_search first; for multi-step web work (sites needing clicks, "
    "forms, logins) use browser_task with a clear step-by-step task; store "
    "important long-term facts with agent_memory_save and check "
    "agent_memory_search when recall_memory comes up empty; run_shell for anything "
    "else on this PC (cmd and PowerShell both available). After tools return, "
    "reply concisely (1-4 sentences) with the actual result. If a tool "
    "returns NEEDS_CONFIRM or REFUSED, tell the user plainly and stop. Never "
    "claim you did something if the tool returned an error."
)


# Tools whose success result needs no follow-up LLM call to phrase — the
# tool result already IS the outcome. Cutting the second GLM call saves
# ~2s on the most common voice commands ("open chrome", "press enter").
_FF_TOOLS = {"launch_app", "press_key", "type_text", "click_at", "set_reminder"}
_FF_BAD = ("ERROR", "REFUSED", "NEEDS_CONFIRM", "DENIED")


# Ready-made spoken replies for instant single-tool turns — varied so the
# voice never sounds like a broken record. Pick is deterministic per
# (tool, target, ~90s bucket): no LLM call, no latency cost, still drifts
# naturally over time instead of repeating one sentence forever.
_FF_LINES = {
    "launch_app": (
        "Done, sir — opening {t}.",
        "Opening {t} now.",
        "{t} it is — one second.",
        "Ho gaya — {t} khul raha hai.",
    ),
    "press_key": (
        "Done, sir — pressed {t}.",
        "Pressed {t}.",
        "Ho gaya — {t} press kar diya.",
    ),
    "type_text": (
        "Done, sir — typed it in.",
        "Typed and done.",
        "Likh diya.",
    ),
    "click_at": (
        "Done, sir — clicked.",
        "Clicked.",
        "Kar diya.",
    ),
}


def _ff_reply(name: str, args: dict, result) -> str | None:
    """Ready-made spoken reply for instant single-tool turns (or None)."""
    if name not in _FF_TOOLS or not isinstance(result, str):
        return None
    if result.startswith(_FF_BAD):
        return None
    if name == "set_reminder":
        return result  # already returns a clean sentence
    lines = _FF_LINES.get(name)
    if not lines:
        return None
    if name == "launch_app":
        t = str(args.get("target", "")).strip().rstrip("/")
        if t.lower().endswith(".exe"):
            t = t[:-4]  # say "calc", not "calc dot exe"
        if not t:
            return None
    else:
        t = str(args.get("key", "the key")).strip() or "the key"
        if name != "press_key":
            t = ""  # type_text / click_at: no target to interpolate
    idx = (abs(hash((name, t.lower() or name))) + int(time.time() // 90)) % len(lines)
    return lines[idx].replace("{t}", t)


def run_agent(cfg, mem, user_text: str, history: list, confirm_fn=None,
              max_iter: int = 8) -> str:
    """Full agentic turn. Returns final reply text."""
    persona_block = cfg.get("_agent_system")
    messages = [{"role": "system", "content": persona_block}]
    messages += history
    messages.append({"role": "user", "content": user_text})

    for i in range(max_iter):
        msg = llm.chat_raw(cfg, messages, tools=TOOLS)
        calls = msg.get("tool_calls") or []
        if not calls:
            return msg.get("content") or "(empty reply)"
        messages.append({"role": "assistant", "content": msg.get("content") or "",
                         "tool_calls": calls})
        for tc in calls:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            log.info("tool call %s %s", fn.get("name"), json.dumps(args)[:120])
            result = _execute(fn.get("name", ""), args, mem, confirm_fn)
            log.info("tool result %s: %.120s", fn.get("name"), result)
            messages.append({"role": "tool", "tool_call_id": tc.get("id"),
                             "content": result[:4000]})
        if i == 0 and len(calls) == 1:
            fast = _ff_reply(fn.get("name", ""), args, result)
            if fast:
                log.info("fast-path reply (answer LLM call skipped)")
                return fast
    return ("I hit my step limit before finishing, sir — here is where things "
            "stand; ask me to continue if you want.")
