"""Persona + system prompt composition.

The personality lives in persona.md — edit that file, never this code.
The system prompt stays LEAN: facts + summary + persona, nothing else.
"""
import re
from pathlib import Path

_FALLBACK_PERSONA = (
    "You are SKY, the user's personal AI assistant, styled after JARVIS. "
    "Dry British wit, address the user as 'sir', be concise."
)

_FACTS_CAP = 600
_SUMMARY_CAP = 700
_KNOWLEDGE_CAP = 6000
_LEARNED_CAP = 900


def load_knowledge(path) -> str:
    """Permanent core knowledge (Spy-style knowledge.txt), or ''. """
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def load_persona(path) -> str:
    try:
        text = Path(path).read_text(encoding="utf-8")
        # drop the title line and blank preamble for a leaner prompt
        lines = [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
        return "\n".join(lines).strip() or _FALLBACK_PERSONA
    except OSError:
        return _FALLBACK_PERSONA


def system_prompt(persona_text: str, facts_block: str, summary: str | None,
                  knowledge: str | None = None, learned: str | None = None) -> str:
    parts = [persona_text.strip()]

    if knowledge:
        parts.append("=== CORE KNOWLEDGE (permanent fundamentals you know) ===")
        parts.append(knowledge[:_KNOWLEDGE_CAP])

    parts.append("What you remember about the user (believe newer statements over these):")
    parts.append(facts_block[:_FACTS_CAP])

    if learned:
        parts.append("=== FRESH FROM THE WEB (background learning — recent, "
                     "not verified; use web_search to confirm) ===")
        parts.append(learned[:_LEARNED_CAP])

    if summary:
        parts.append("Older conversation summary:")
        parts.append(summary[:_SUMMARY_CAP])

    parts.append(
        "हर जवाब के अंत में ठीक दो लेबल लाइनें दो — TYPED: (English, chat "
        "style) और SPOKEN: (शुद्ध हिंदी देवनागरी में, बोलने लायक, bina "
        "markdown/emoji)। दोनों का मतलब same; लेबल के बाहर कुछ नहीं। केवल "
        "कमांड/पाथ/कोड Latin में। संक्षिप्त रहो।"
    )
    return "\n".join(parts)


_TAG = re.compile(r"^\s*(TYPED|SPOKEN)\s*[:\-]\s*(.*)$", re.I)


def split_bilingual(text: str) -> tuple[str, str]:
    """Split a persona reply into (say, show).

    SPOKEN: line(s) -> what the voice speaks (shuddh Hindi).
    TYPED: line(s) -> what appears on screen (English).
    Labels missing -> the raw text is used for both.
    """
    say_parts: list[str] = []
    show_parts: list[str] = []
    mode: str | None = None
    for ln in (text or "").splitlines():
        m = _TAG.match(ln)
        if m:
            mode = m.group(1).lower()
            first = m.group(2).strip()
            if first:
                (say_parts if mode == "spoken" else show_parts).append(first)
            continue
        if mode == "spoken":
            say_parts.append(ln)
        elif mode == "typed":
            show_parts.append(ln)
        else:
            say_parts.append(ln)
            show_parts.append(ln)
    say = " ".join(p.strip() for p in say_parts if p.strip()).strip()
    show = "\n".join(p.rstrip() for p in show_parts if p.strip()).strip()
    if not say and not show:
        say = show = (text or "").strip()
    if not say:
        say = show
    if not show:
        show = say
    return say, show
