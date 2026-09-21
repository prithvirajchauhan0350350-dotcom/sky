"""
spy_ops.py — J.A.R.V.I.S. operations layer for Spy.
---------------------------------------------------
The "manage systems / automate tasks" slice of the personal-assistant
fantasy, built on the standard library only:

  * REMINDERS  — persistent, SQLite-backed (same spy_memory.db), with
                 flexible time parsing ("in 20 minutes", "at 6pm",
                 "tomorrow 9am", ISO). A watchdog thread in spy.py polls
                 due_reminders() and Spy ANNOUNCES them aloud — the
                 "anticipates needs before they are spoken" part.
  * EMAIL      — sends mail through the user's own account via SMTP.
                 Credentials come from spy.env (SPY_EMAIL,
                 SPY_EMAIL_PASSWORD, optional SPY_SMTP_HOST/PORT);
                 nothing is hard-coded and the password is never echoed.
  * FILE TIDY  — organizes a messy folder into category subfolders
                 (Images, Documents, Music, ...) — dry-run by default,
                 moves only when the user confirms apply.
  * SMART HOME — Philips Hue bridge over the local network (pure urllib;
                 SPY_HUE_BRIDGE + SPY_HUE_TOKEN in spy.env). Lights on/
                 off/brightness/color, device listing. Never required.

Every outward-facing or mutating action returns a clear message; Spy's
confirmation gate in spy.py is the final safety layer.
"""

import os
import re
import shutil
import smtplib
import sqlite3
import ssl
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from email.message import EmailMessage

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "spy_memory.db")
ENV_PATH = os.path.join(SCRIPT_DIR, "spy.env")

# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load_env() -> dict:
    """Parse spy.env (KEY=VALUE lines) into a dict. Missing file = {}."""
    out = {}
    try:
        with open(ENV_PATH, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    out[key.strip()] = val.strip()
    except OSError:
        pass
    return out


def _db():
    """Own connection to the shared database (WAL mode tolerates several)."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ---------------------------------------------------------------------------
# reminders
# ---------------------------------------------------------------------------

def _init_reminders(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS reminders ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " text TEXT NOT NULL,"
        " due_at TEXT NOT NULL,"
        " created_at TEXT NOT NULL,"
        " announced INTEGER NOT NULL DEFAULT 0)"
    )
    conn.commit()


_IN_UNITS = {"sec": 1, "secs": 1, "second": 1, "seconds": 1,
             "min": 60, "mins": 60, "minute": 60, "minutes": 60,
             "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600,
             "day": 86400, "days": 86400,
             "week": 604800, "weeks": 604800}
_IN_RE = re.compile(
    r"\b(\d+)\s*(second|seconds|sec|secs|minute|minutes|min|mins|"
    r"hour|hours|hr|hrs|day|days|week|weeks)\b", re.I)
_TIME_RE = re.compile(r"\b(\d{1,2})[:.](\d{2})\s*(am|pm)?\b|\b(\d{1,2})\s*(am|pm)\b", re.I)


def _parse_when(when: str):
    """Parse a flexible time expression → datetime. Raises ValueError."""
    now = datetime.now()
    text = (when or "").strip().lower()
    if not text:
        raise ValueError("no time given")

    # ISO-ish: "2026-09-18 15:00" / "2026-09-18T15:00" / "2026-09-18"
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text, fmt)
            if fmt == "%Y-%m-%d":
                dt = dt.replace(hour=9)
            return dt
        except ValueError:
            pass

    target = now
    if re.search(r"\btomorrow\b", text):
        target += timedelta(days=1)
        text = re.sub(r"\btomorrow\b", " ", text)

    # relative: "in 10 minutes", "in 1 hour 30 minutes"
    delta = 0
    for amount, unit in _IN_RE.findall(text):
        delta += int(amount) * _IN_UNITS[unit.lower()]
    if delta:
        return target + timedelta(seconds=delta)

    # clock time: "at 6pm", "18:30", "6:30 am", "9 pm", "9am"
    m = _TIME_RE.search(text)
    if m:
        hh = int(m.group(1) or m.group(4))
        mm = int(m.group(2) or 0)
        ap = (m.group(3) or m.group(5) or "").lower()
        if ap == "pm" and hh < 12:
            hh += 12
        if ap == "am" and hh == 12:
            hh = 0
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError(f"bad time: {hh}:{mm:02d}")
        dt = target.replace(hour=hh, minute=mm, second=0, microsecond=0)
        # weekday+time ("friday 6pm"): roll forward to the next named day
        for i, day in enumerate(("monday", "tuesday", "wednesday", "thursday",
                                 "friday", "saturday", "sunday")):
            if re.search(rf"\b{day}\b", text):
                while dt.weekday() != i or dt <= now - timedelta(minutes=1):
                    dt += timedelta(days=1)
                break
        if dt <= now - timedelta(minutes=1):  # already past → tomorrow
            dt += timedelta(days=1)
        return dt

    # weekday name: "on monday" (defaults to 9:00; "monday 6pm" is caught
    # by the clock branch above only if the clock precedes the weekday —
    # for weekday+time, users say e.g. "monday at 6pm", so try both)
    for i, day in enumerate(("monday", "tuesday", "wednesday", "thursday",
                             "friday", "saturday", "sunday")):
        if re.search(rf"\b{day}\b", text):
            days_ahead = (i - target.weekday()) % 7 or 7
            dt = (target + timedelta(days=days_ahead)).replace(
                hour=9, minute=0, second=0, microsecond=0)
            tm = _TIME_RE.search(text)
            if tm:
                hh = int(tm.group(1) or tm.group(4))
                mm = int(tm.group(2) or 0)
                ap = (tm.group(3) or tm.group(5) or "").lower()
                if ap == "pm" and hh < 12:
                    hh += 12
                if ap == "am" and hh == 12:
                    hh = 0
                dt = dt.replace(hour=hh, minute=mm)
            return dt

    raise ValueError(f"couldn't understand the time: {when!r}")


def _humanize_delta(dt: datetime) -> str:
    secs = int((dt - datetime.now()).total_seconds())
    past = secs < 0
    secs = abs(secs)
    if secs < 60:
        out = f"{secs}s"
    elif secs < 3600:
        out = f"{secs // 60}m {secs % 60:02d}s"
    elif secs < 86400:
        out = f"{secs // 3600}h {(secs % 3600) // 60:02d}m"
    else:
        out = f"{secs // 86400}d {(secs % 86400) // 3600}h"
    return ("in " + out) if not past else (out + " ago")


def add_reminder(text: str, when: str) -> dict:
    """Store a reminder. Fires once when due; the watchdog announces it."""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "message": "I need to know what to remind you about."}
    try:
        due = _parse_when(when)
    except ValueError as e:
        return {"ok": False, "message": str(e)}
    if due < datetime.now() - timedelta(seconds=30):
        return {"ok": False, "message": "That time is in the past, sir."}
    conn = _db()
    try:
        _init_reminders(conn)
        conn.execute(
            "INSERT INTO reminders (text, due_at, created_at) VALUES (?,?,?)",
            (text, due.isoformat(timespec="seconds"), _now_iso()))
        conn.commit()
        rid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    finally:
        conn.close()
    nice = due.strftime("%a %d %b, %H:%M")
    return {"ok": True,
            "message": f"Reminder #{rid} set: “{text}” — due {nice} "
                       f"({_humanize_delta(due)}). I'll speak up when it's time."}


def list_reminders() -> dict:
    conn = _db()
    try:
        _init_reminders(conn)
        rows = conn.execute(
            "SELECT id, text, due_at FROM reminders "
            "WHERE announced=0 ORDER BY due_at").fetchall()
    finally:
        conn.close()
    if not rows:
        return {"ok": True, "message": "No pending reminders — all quiet."}
    lines = []
    for rid, text, due_iso in rows:
        try:
            due = datetime.fromisoformat(due_iso)
            when = f"{due.strftime('%a %d %b %H:%M')} ({_humanize_delta(due)})"
        except ValueError:
            when = due_iso
        lines.append(f"  #{rid}  {when} — {text}")
    return {"ok": True,
            "message": f"{len(rows)} pending reminder(s):\n" + "\n".join(lines)}


def delete_reminder(target) -> dict:
    """target = reminder id, or a text fragment to match against."""
    conn = _db()
    try:
        _init_reminders(conn)
        if isinstance(target, int) or str(target).isdigit():
            cur = conn.execute("DELETE FROM reminders WHERE id=? AND announced=0",
                               (int(target),))
        else:
            cur = conn.execute(
                "DELETE FROM reminders WHERE announced=0 AND text LIKE ?",
                (f"%{target}%",))
        conn.commit()
        n = cur.rowcount
    finally:
        conn.close()
    if n:
        return {"ok": True, "message": f"Removed {n} reminder(s)."}
    return {"ok": False, "message": "No pending reminder matched that."}


def due_reminders() -> list:
    """Called by the watchdog: reminders now due (marks them announced so
    each fires exactly once). Missed ones (app was closed) still surface."""
    conn = _db()
    try:
        _init_reminders(conn)
        now = _now_iso()
        rows = conn.execute(
            "SELECT id, text, due_at FROM reminders "
            "WHERE announced=0 AND due_at <= ? ORDER BY due_at",
            (now,)).fetchall()
        if rows:
            conn.execute(
                "UPDATE reminders SET announced=1 WHERE announced=0 AND due_at <= ?",
                (now,))
            conn.commit()
    finally:
        conn.close()
    out = []
    for rid, text, due_iso in rows:
        late = ""
        try:
            dt = datetime.fromisoformat(due_iso)
            if datetime.now() - dt > timedelta(minutes=2):
                late = f" (due {dt.strftime('%H:%M')})"
        except ValueError:
            pass
        out.append({"id": rid, "text": text, "late_note": late})
    return out


def _housekeep_reminders():
    """Drop announced reminders older than 30 days (called on list)."""
    conn = _db()
    try:
        _init_reminders(conn)
        cutoff = (datetime.now() - timedelta(days=30)).isoformat(timespec="seconds")
        conn.execute("DELETE FROM reminders WHERE announced=1 AND due_at < ?",
                     (cutoff,))
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# email — through the user's own account (spy.env config)
# ---------------------------------------------------------------------------

_SMTP_PRESETS = {
    "gmail.com": ("smtp.gmail.com", 465, "ssl"),
    "googlemail.com": ("smtp.gmail.com", 465, "ssl"),
    "outlook.com": ("smtp-mail.outlook.com", 587, "starttls"),
    "hotmail.com": ("smtp-mail.outlook.com", 587, "starttls"),
    "live.com": ("smtp-mail.outlook.com", 587, "starttls"),
    "yahoo.com": ("smtp.mail.yahoo.com", 465, "ssl"),
    "icloud.com": ("smtp.mail.me.com", 587, "starttls"),
}


def _smtp_settings(env: dict):
    addr = env.get("SPY_EMAIL", "")
    pwd = env.get("SPY_EMAIL_PASSWORD", "")
    if not addr or not pwd:
        return None, ("Email isn't configured yet. Add these two lines to "
                      "spy.env (next to spy.py) and restart me:\n"
                      "  SPY_EMAIL=you@gmail.com\n"
                      "  SPY_EMAIL_PASSWORD=your-app-password\n"
                      "(Gmail needs an App Password: Google Account → "
                      "Security → 2-Step Verification → App passwords.)")
    if "@" not in addr:
        return None, {"ok": False, "message": "SPY_EMAIL in spy.env doesn't look like an email address."}
    domain = addr.rsplit("@", 1)[1].lower()
    host, port, mode = _SMTP_PRESETS.get(domain, ("", 0, ""))
    host = env.get("SPY_SMTP_HOST", host)
    try:
        port = int(env.get("SPY_SMTP_PORT", port))
    except ValueError:
        port = 0
    mode = env.get("SPY_SMTP_MODE", mode) or ("ssl" if port == 465 else "starttls")
    return (addr, pwd, host, port, mode), None


def send_email(to: str, subject: str, body: str) -> dict:
    """Send an email from the user's own configured account."""
    to = (to or "").strip()
    subject = (subject or "").strip() or "(no subject)"
    if not to or "@" not in to:
        return {"ok": False, "message": "That recipient address doesn't look valid."}
    settings, err = _smtp_settings(_load_env())
    if err:
        return {"ok": False, "message": err} if isinstance(err, str) else err
    addr, pwd, host, port, mode = settings
    if not host or not port:
        return {"ok": False, "message":
                f"I don't know the SMTP server for {addr.rsplit('@',1)[1]} — "
                "add SPY_SMTP_HOST and SPY_SMTP_PORT to spy.env."}
    msg = EmailMessage()
    msg["From"] = addr
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body or "")
    try:
        if mode == "ssl":
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=25) as s:
                s.login(addr, pwd)
                s.send_message(msg)
        else:
            ctx = ssl.create_default_context()
            with smtplib.SMTP(host, port, timeout=25) as s:
                s.ehlo()
                s.starttls(context=ctx)
                s.login(addr, pwd)
                s.send_message(msg)
        return {"ok": True,
                "message": f"Email sent to {to} — subject “{subject}”."}
    except smtplib.SMTPAuthenticationError:
        return {"ok": False, "message":
                "The mail server rejected the login. Check SPY_EMAIL / "
                "SPY_EMAIL_PASSWORD in spy.env (Gmail wants an App Password, "
                "not the normal password)."}
    except Exception as e:
        return {"ok": False, "message": f"Couldn't send the email: {e}"}


# ---------------------------------------------------------------------------
# file tidy — organize a folder into category subfolders
# ---------------------------------------------------------------------------

_TIDY_CATEGORIES = (
    ("Images",    ".jpg .jpeg .png .gif .bmp .webp .heic .svg .tiff .ico .raw"),
    ("Documents", ".pdf .doc .docx .txt .md .rtf .odt .xls .xlsx .csv .ppt .pptx"),
    ("Music",     ".mp3 .wav .flac .m4a .aac .ogg .wma"),
    ("Video",     ".mp4 .mkv .avi .mov .wmv .webm .flv"),
    ("Archives",  ".zip .rar .7z .tar .gz .bz2 .xz .iso"),
    ("Code",      ".py .js .html .css .java .c .cpp .h .ipynb .json .xml .ts .sh .bat .ps1"),
    ("Installers", ".exe .msi .apk .dmg .appx"),
)

# files of Spy's own app that must never be shuffled
_TIDY_NEVER = {"spy.py", "spy.env", "spy_memory.db", "spy_core_knowledge.txt"}


def _category_for(ext: str) -> str:
    for cat, exts in _TIDY_CATEGORIES:
        if ext in exts.split():
            return cat
    return "Other"


def organize_folder(path: str, apply: bool = False) -> dict:
    """Plan (default) or perform a tidy of one folder. Dry-run lists the
    proposed moves; apply=True performs them. Never descends into
    subfolders and never touches Spy's own files."""
    path = os.path.expanduser((path or "").strip())
    if not path:
        return {"ok": False, "message": "Which folder should I tidy?"}
    if not os.path.isdir(path):
        return {"ok": False, "message": f"No such folder: {path}"}
    plan = []  # (filename, category)
    try:
        entries = os.listdir(path)
    except OSError as e:
        return {"ok": False, "message": f"Can't read the folder: {e}"}
    for name in entries:
        full = os.path.join(path, name)
        if not os.path.isfile(full) or name.startswith(".") or name.lower() in _TIDY_NEVER:
            continue
        ext = os.path.splitext(name)[1].lower()
        if not ext:
            continue
        cat = _category_for(ext)
        if cat != "Other" or True:  # even "Other" files get a home
            plan.append((name, cat))
    if not plan:
        return {"ok": True, "message": "Nothing to organize there — already tidy."}
    if not apply:
        sample = plan[:12]
        preview = "\n".join(f"  {n} → {c}/" for n, c in sample)
        more = f"\n  … and {len(plan) - 12} more" if len(plan) > 12 else ""
        return {"ok": True, "apply": False,
                "message": f"Dry run — {len(plan)} file(s) would move:\n"
                           + preview + more +
                           "\nSay the word and I'll do it (apply=true)."}
    moved, errors = 0, []
    for name, cat in plan:
        cat_dir = os.path.join(path, cat)
        dest = os.path.join(cat_dir, name)
        try:
            os.makedirs(cat_dir, exist_ok=True)
            if os.path.exists(dest):  # keep both: suffix with (2), (3)…
                base, ext = os.path.splitext(name)
                i = 2
                while os.path.exists(os.path.join(cat_dir, f"{base} ({i}){ext}")):
                    i += 1
                dest = os.path.join(cat_dir, f"{base} ({i}){ext}")
            shutil.move(os.path.join(path, name), dest)
            moved += 1
        except OSError as e:
            errors.append(f"{name}: {e}")
    summary = f"Tidied: {moved} file(s) moved into category folders."
    if errors:
        summary += f"\n{len(errors)} problem(s): " + "; ".join(errors[:5])
    return {"ok": True, "apply": True, "message": summary}


# ---------------------------------------------------------------------------
# smart home — Philips Hue bridge (local network, stdlib urllib)
# ---------------------------------------------------------------------------

def _hue_base(env: dict):
    ip = env.get("SPY_HUE_BRIDGE", "").strip()
    token = env.get("SPY_HUE_TOKEN", "").strip()
    if not ip or not token:
        return None, ("Smart home isn't linked yet. Find your Hue Bridge's "
                      "IP (router app or 'ping hue' usually resolves it), "
                      "press its link button, then add to spy.env:\n"
                      "  SPY_HUE_BRIDGE=192.168.x.x\n"
                      "  SPY_HUE_TOKEN=<the token from "
                      "http://<ip>/debug/clip after pressing link>\n"
                      "Then I can run the lights, sir.")
    return f"http://{ip}/api/{token}", None


def _hue_call(method: str, path: str, body: dict | None = None, timeout=6):
    url = path if path.startswith("http") else _HUE_BASE + path
    data = None
    if body is not None:
        import json as _json
        data = _json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


_HUE_BASE = ""  # set per call via _hue()

_HUE_COLORS = {  # name → (hue 0-65535, sat 0-254)
    "red": (0, 254), "orange": (7000, 254), "yellow": (12000, 254),
    "green": (25000, 254), "cyan": (34000, 254), "blue": (47000, 254),
    "purple": (50000, 254), "pink": (56000, 254), "white": (15000, 0),
}


def _hue():
    base, err = _hue_base(_load_env())
    if err:
        return None, err
    global _HUE_BASE
    _HUE_BASE = base
    return base, None


def smart_home(device: str = "", action: str = "", value=None) -> dict:
    """Control a smart-home device (Philips Hue lights). action: list |
    on | off | brightness | color. value: 0-100 for brightness, a color
    name for color."""
    ok, err = _hue()
    if err:
        return {"ok": False, "message": err}
    device = (device or "").strip().lower()
    action = (action or "list").strip().lower()
    try:
        import json as _json
        raw = _hue_call("GET", "/lights")
        lights = _json.loads(raw)
    except Exception as e:
        return {"ok": False, "message": f"Couldn't reach the Hue bridge: {e}"}
    if not isinstance(lights, dict) or not lights:
        return {"ok": True, "message": "The bridge answered but reports no lights."}

    def find_lid():
        if not device:
            return None
        for lid, info in sorted(lights.items()):
            if device in str(info.get("name", "")).lower() or device == str(lid):
                return lid
        return None

    if action == "list" or (not device and action not in ("on", "off")):
        lines = [f"  {lid}: {info.get('name', '?')} "
                 f"({'on' if info.get('state', {}).get('on') else 'off'})"
                 for lid, info in sorted(lights.items())]
        return {"ok": True, "message": "Lights I can control:\n" + "\n".join(lines)}

    lid = find_lid()
    if lid is None:
        return {"ok": False, "message": f"No light matches “{device}”. "
                "Say 'list the lights' to see their names."}
    state = {}
    if action == "on":
        state["on"] = True
    elif action == "off":
        state["on"] = False
    elif action in ("brightness", "dim"):
        try:
            state["bri"] = max(1, min(254, int(float(value) / 100 * 254)))
            state["on"] = True
        except (TypeError, ValueError):
            return {"ok": False, "message": "Brightness needs a number 0-100."}
    elif action == "color":
        key = str(value or "").strip().lower()
        if key not in _HUE_COLORS:
            return {"ok": False, "message":
                    f"Unknown color “{value}” — I know: "
                    + ", ".join(sorted(_HUE_COLORS))}
        hue, sat = _HUE_COLORS[key]
        state.update(hue=hue, sat=sat, on=True)
    else:
        return {"ok": False, "message": f"Unknown smart-home action: {action}"}
    try:
        _hue_call("PUT", f"/lights/{lid}/state", state)
        return {"ok": True, "message": f"Done — light “{lights[lid].get('name', lid)}” updated."}
    except Exception as e:
        return {"ok": False, "message": f"The bridge refused: {e}"}
