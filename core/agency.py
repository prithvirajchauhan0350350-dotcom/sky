"""Agency roster — installable specialist personas for SKY.

The roster (264 specialists, 18 divisions) lives in agency/ and is generated
from github.com/msitarzewski/agency-agents by that repo's scripts/sync_sky.py.
Activating a specialist writes data/agency_active.json; sky.agent_turn then
appends the agent's full persona to the system prompt, so subsequent chat
turns run in that specialist mode. Stand-down restores plain SKY.

Edit nothing here to change personalities — the roster md files are the truth.
"""
from __future__ import annotations

import json
import time
from pathlib import Path


def roster(root) -> dict:
    f = Path(root) / "agency" / "roster.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {"divisions": [], "agents": []}


def active(root) -> dict | None:
    """Currently activated specialist (validated against the roster) or None."""
    f = Path(root) / "data" / "agency_active.json"
    try:
        rec = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None
    slug = rec.get("slug", "")
    if not any(a["slug"] == slug for a in roster(root).get("agents", [])):
        return None  # roster changed since activation — treat as inactive
    return rec


def activate(root, slug: str) -> dict:
    """Activate a specialist by slug. Raises ValueError on unknown slug."""
    meta = next((a for a in roster(root).get("agents", [])
                 if a["slug"] == slug), None)
    if meta is None:
        raise ValueError(f"unknown specialist: {slug!r}")
    rec = {"slug": slug, "name": meta["name"], "division": meta["division"],
           "ts": time.time()}
    f = Path(root) / "data" / "agency_active.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return rec


def deactivate(root) -> bool:
    f = Path(root) / "data" / "agency_active.json"
    try:
        f.unlink()
        return True
    except FileNotFoundError:
        return False


def override_block(root) -> str:
    """System-prompt block for the active specialist ('' if none)."""
    rec = active(root)
    if not rec:
        return ""
    meta = next((a for a in roster(root).get("agents", [])
                 if a["slug"] == rec["slug"]), None)
    if not meta:
        return ""
    f = Path(root) / "agency" / meta["file"]
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return (
        "# ACTIVE SPECIALIST MODE — " + meta["name"].upper() + " (" +
        meta["division"] + ")\n" + text.strip() +
        "\n\n# Specialist-mode guardrails\n"
        "- You remain SKY: keep your persona, your voice, the user's language "
        "(Hinglish/English as they use), and every safety and tool rule.\n"
        "- Layer this specialist's expertise, workflow and deliverables ON TOP "
        "of your personality; do not lose your identity or memory.\n"
        "- Open with one short line acknowledging the specialist mode is live, "
        "then work the problem."
    )
