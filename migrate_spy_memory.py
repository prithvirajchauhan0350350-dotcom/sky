#!/usr/bin/env python3
"""One-shot migration: Spy's memory (Desktop spy_memory.db) -> SKY memory
(data/memory.db next to this script). Runs on Windows Python.

- Spy facts       -> SKY facts (kind='general', INSERT OR IGNORE by value)
- Spy knowledge   -> SKY general facts, tagged "learned: ..." (newest 60)
- Spy reminders   -> not migrated (SKY's reminders table is the live one)
- Spy chats       -> not migrated (different format; history stays fresh)

Idempotent: re-running adds nothing new. Existing SKY facts are never touched.
"""
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPY_DB = Path(r"C:\Users\Svelt\Desktop\spy_memory.db")
SKY_DB = ROOT / "data" / "memory.db"

MAX_KNOWLEDGE = 60  # keep the migration lean; SKY's prompt only shows 10


def main():
    if not SPY_DB.exists():
        print(f"Spy DB not found: {SPY_DB}")
        sys.exit(1)
    src = sqlite3.connect(f"file:{SPY_DB}?mode=ro", uri=True)
    SKY_DB.parent.mkdir(parents=True, exist_ok=True)
    sky = sqlite3.connect(SKY_DB)
    # make sure the schema exists even if SKY never ran yet
    sky.executescript(
        """
        CREATE TABLE IF NOT EXISTS facts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            key  TEXT,
            value TEXT NOT NULL,
            ts REAL NOT NULL
        );
        """)
    now = time.time()

    facts = [r[0] for r in src.execute("SELECT text FROM facts ORDER BY id ASC")]
    added_f = 0
    for text in facts:
        text = (text or "").strip()
        if not text:
            continue
        cur = sky.execute(
            "INSERT OR IGNORE INTO facts(kind, key, value, ts) "
            "SELECT 'general', NULL, ?, ? WHERE NOT EXISTS ("
            "  SELECT 1 FROM facts WHERE kind='general' AND value=?)",
            (text, now, text))
        added_f += cur.rowcount

    knowledge = [r[0] for r in
                 src.execute("SELECT text FROM knowledge ORDER BY id DESC LIMIT ?",
                             (MAX_KNOWLEDGE,))]
    added_k = 0
    for text in reversed(knowledge):  # oldest first so newest ends up newest
        text = (text or "").strip()
        if not text:
            continue
        val = f"learned: {text}"[:300]
        cur = sky.execute(
            "INSERT OR IGNORE INTO facts(kind, key, value, ts) "
            "SELECT 'general', NULL, ?, ? WHERE NOT EXISTS ("
            "  SELECT 1 FROM facts WHERE kind='general' AND value=?)",
            (val, now, val))
        added_k += cur.rowcount

    sky.commit()
    total = sky.execute("SELECT COUNT(*) FROM facts WHERE kind='general'").fetchone()[0]
    print(f"migrated: {added_f} facts, {added_k} learned notes "
          f"(SKY now holds {total} general facts)")
    for (v,) in sky.execute("SELECT value FROM facts WHERE kind='general' ORDER BY id DESC LIMIT 8"):
        print("  •", v[:90])
    sky.close()
    src.close()


if __name__ == "__main__":
    main()
