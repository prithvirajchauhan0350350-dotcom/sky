"""Secret redaction — SKY never stores, logs, or echoes live credentials."""

import re

_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9_\-]{16,}"), "sk-***REDACTED***"),
    (re.compile(r"ghp_[A-Za-z0-9]{16,}"), "ghp_***REDACTED***"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{16,}"), "github_pat_***REDACTED***"),
    (re.compile(r"AKIA[0-9A-Z]{16,}"), "AKIA***REDACTED***"),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9_\-.=+/]{12,}"), r"\1 ***REDACTED***"),
    (re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\b\s*[:=]\s*\S{8,}"),
     r"\1 = ***REDACTED***"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-.]{20,}\b"), "eyJ***REDACTED***"),  # JWT
]


def redact(text) -> str:
    """Mask API keys / bearer tokens / JWTs / password assignments in text."""
    if not text:
        return text
    out = str(text)
    for rx, repl in _PATTERNS:
        out = rx.sub(repl, out)
    return out
