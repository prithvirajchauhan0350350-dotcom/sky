"""Persistent memory: SQLite at data/memory.db. Survives restarts.

Three stores:
- facts    identity facts (name, lives, work, birthday — newest wins per key)
           + general facts (likes, dislikes, "remember that ..." — appended, capped)
- messages rolling chat log (capped)
- summary  one rolling summary of evicted old messages

Thread-safe via a single lock; WAL journal mode.
"""
import logging
import re
import sqlite3
import threading
import time
from pathlib import Path

log = logging.getLogger("sky.memory")

_IDENTITY_KEYS = ("name", "lives", "work", "birthday")

# (identity_key_or_None, regex) — first capture group is the value
_PATTERNS = [
    ("name", r"\b(?:my name is|call me)\s+([A-Za-z][\w'\-]*(?:\s+[A-Za-z][\w'\-]*)?)"),
    ("name", r"\bmera naam\s+([A-Za-z][\w'\-]*(?:\s+[A-Za-z][\w'\-]*)?)\s+hai"),
    ("birthday", r"\bmy birthday is\s+(.{3,40}?)\s*(?:[.!?,]|$)"),
    ("lives", r"\bi (?:live in|am from)\s+([A-Za-z][\w ,.'\-]{1,40}?)\s*(?:[.!?,]|$)"),
    ("work", r"\bi (?:work at|work for|work as)\s+([A-Za-z][\w ,.'\-]{1,40}?)\s*(?:[.!?,]|$)"),
    (None, r"\bi (?:like|love|enjoy)\s+([A-Za-z][\w ,.'\-]{1,60}?)\s*(?:[.!?,]|$)"),
    (None, r"\bi (?:hate|dislike|can't stand)\s+([A-Za-z][\w ,.'\-]{1,60}?)\s*(?:[.!?,]|$)"),
    (None, r"\bremember (?:that )?(.{3,120}?)\s*(?:[.!?,]|$)"),
]

_TRAILING_FILLER = {"hai", "hun", "hoon", "rehta", "rehti", "tha", "the", "naam"}
# capture stops at the first of these words ("Prithvi and I love X" -> "Prithvi")
_STOPWORDS = {"and", "i", "im", "but", "is", "am", "the", "my", "me", "who",
              "was", "so", "just", "aur", "main", "mai", "or"}


class Memory:
    def __init__(self, db_path):
        self.db_path = str(db_path)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self):
        with self.lock, self.conn:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS facts(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,        -- 'identity' | 'general'
                    key  TEXT,                 -- identity key, NULL for general
                    value TEXT NOT NULL,
                    ts REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    ts REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS summary(
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    text TEXT,
                    updated_at REAL
                );
                CREATE TABLE IF NOT EXISTS reminders(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    text TEXT NOT NULL,
                    due_ts REAL NOT NULL,
                    fired INTEGER NOT NULL DEFAULT 0,
                    ts REAL NOT NULL
                );
                """
            )

    # ---------------- facts ----------------

    @staticmethod
    def _tidy(value: str) -> str:
        words = value.strip().split()
        out = []
        for w in words:
            if w.lower().strip(".,!?") in _STOPWORDS:
                break
            out.append(w)
        while out and out[-1].lower() in _TRAILING_FILLER:
            out.pop()
        return " ".join(out)[:80]

    def extract_facts(self, text: str) -> list:
        """Regex auto-extraction. Runs BEFORE the prompt is built so a fact
        stated this turn is already known. Returns list of (kind, key, value)."""
        found = []
        for key, pat in _PATTERNS:
            m = re.search(pat, text, flags=re.IGNORECASE)
            if not m:
                continue
            value = self._tidy(m.group(1))
            if not value or len(value) < 2:
                continue
            if key in _IDENTITY_KEYS:
                self.add_identity(key, value)
                found.append(("identity", key, value))
            else:
                self.add_general(value)
                found.append(("general", None, value))
        return found

    def add_identity(self, key: str, value: str):
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM facts WHERE kind='identity' AND key=?", (key,))
            self.conn.execute(
                "INSERT INTO facts(kind, key, value, ts) VALUES('identity',?,?,?)",
                (key, value, time.time()),
            )

    def add_general(self, value: str, cap: int = 100):
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO facts(kind, key, value, ts) VALUES('general',NULL,?,?)",
                (value, time.time()),
            )
            # keep newest `cap` general facts
            self.conn.execute(
                """DELETE FROM facts WHERE kind='general' AND id NOT IN
                   (SELECT id FROM facts WHERE kind='general' ORDER BY id DESC LIMIT ?)""",
                (cap,),
            )

    def facts_block(self, general_limit: int = 10) -> str:
        with self.lock:
            ident = self.conn.execute(
                "SELECT key, value FROM facts WHERE kind='identity' ORDER BY id ASC"
            ).fetchall()
            gen = self.conn.execute(
                "SELECT value FROM facts WHERE kind='general' ORDER BY id DESC LIMIT ?",
                (general_limit,),
            ).fetchall()
        lines = [f"- {k}: {v}" for k, v in ident] + [f"- {v[0]}" for v in gen]
        return "\n".join(lines) if lines else "- (nothing yet)"

    def all_facts(self) -> list:
        with self.lock:
            return self.conn.execute(
                "SELECT kind, key, value FROM facts ORDER BY id ASC"
            ).fetchall()

    # ---------------- messages ----------------

    def append_message(self, role: str, content: str, cap: int = 300):
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO messages(role, content, ts) VALUES(?,?,?)",
                (role, content, time.time()),
            )
            self.conn.execute(
                """DELETE FROM messages WHERE id NOT IN
                   (SELECT id FROM messages ORDER BY id DESC LIMIT ?)""",
                (cap,),
            )

    def history(self, n: int = 8) -> list:
        """Last n turns as [(role, content)] oldest-first."""
        with self.lock:
            rows = self.conn.execute(
                "SELECT role, content FROM messages ORDER BY id DESC LIMIT ?", (n,)
            ).fetchall()
        return list(reversed(rows))

    def count(self) -> int:
        with self.lock:
            return self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

    def oldest_beyond(self, keep: int = 8, cap: int = 200) -> tuple:
        """Oldest messages beyond the newest `keep` → (ids, transcript text)."""
        with self.lock:
            total = self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            extra = total - keep
            if extra <= 0:
                return [], ""
            ids = [r[0] for r in self.conn.execute(
                "SELECT id FROM messages ORDER BY id ASC LIMIT ?", (min(extra, cap),)
            )]
            qmarks = ",".join("?" * len(ids))
            rows = self.conn.execute(
                f"SELECT role, content FROM messages WHERE id IN ({qmarks}) ORDER BY id ASC",
                ids,
            ).fetchall()
        return ids, "\n".join(f"{r}: {c}" for r, c in rows)

    def delete_messages(self, ids: list):
        if not ids:
            return
        qmarks = ",".join("?" * len(ids))
        with self.lock, self.conn:
            self.conn.execute(f"DELETE FROM messages WHERE id IN ({qmarks})", ids)

    # ---------------- summary ----------------

    def get_summary(self):
        with self.lock:
            row = self.conn.execute("SELECT text FROM summary WHERE id=1").fetchone()
        return row[0] if row and row[0] else None

    def set_summary(self, text: str, cap: int = 1500):
        with self.lock, self.conn:
            self.conn.execute(
                """INSERT INTO summary(id, text, updated_at) VALUES(1,?,?)
                   ON CONFLICT(id) DO UPDATE SET text=excluded.text, updated_at=excluded.updated_at""",
                (text[:cap], time.time()),
            )

    # ---------------- reminders ----------------

    def add_reminder(self, text: str, due_ts: float):
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO reminders(text, due_ts, fired, ts) VALUES(?,?,0,?)",
                (text, due_ts, time.time()),
            )

    def due_reminders(self, now: float | None = None) -> list:
        now = now or time.time()
        with self.lock:
            return self.conn.execute(
                "SELECT id, text, due_ts FROM reminders WHERE fired=0 AND due_ts<=? "
                "ORDER BY due_ts ASC", (now,)).fetchall()

    def pending_reminders(self) -> list:
        with self.lock:
            return self.conn.execute(
                "SELECT id, text, due_ts FROM reminders WHERE fired=0 AND due_ts>? "
                "ORDER BY due_ts ASC", (time.time(),)).fetchall()

    def mark_fired(self, ids: list):
        if not ids:
            return
        qmarks = ",".join("?" * len(ids))
        with self.lock, self.conn:
            self.conn.execute(f"UPDATE reminders SET fired=1 WHERE id IN ({qmarks})", ids)

    def delete_reminder(self, reminder_id: int) -> bool:
        """Remove a pending reminder outright (user cancelled it)."""
        with self.lock, self.conn:
            cur = self.conn.execute(
                "DELETE FROM reminders WHERE id=? AND fired=0", (reminder_id,))
            return cur.rowcount > 0

    # ---------------- lifecycle ----------------

    def reset_history(self):
        """Clear chat history + summary. Facts about the user are KEPT."""
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM messages")
            self.conn.execute("DELETE FROM summary")

    def wipe(self):
        """Nuke everything (fresh start)."""
        with self.lock, self.conn:
            for t in ("facts", "messages", "summary"):
                self.conn.execute(f"DELETE FROM {t}")

    def close(self):
        with self.lock:
            self.conn.close()
