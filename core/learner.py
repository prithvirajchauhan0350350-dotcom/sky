"""learner.py — Live internet learning for SKY (ported from Spy's web_learn.py).

Gives SKY the ability to read the internet (web search, page fetching, RSS
headlines) and keeps a rolling knowledge store that the chat model sees on
every reply.

Used two ways:
  * Tools: agent.py exposes web_search / read_page to the model so it can
    look things up mid-conversation. Read-only — never needs approval.
  * Background: skyd.py calls learn_once() on a schedule (replaces Spy's
    _background_learner daemon thread). Results land in data/memory.db as
    "learned:" facts.

Standard library only — no extra packages.
"""
import logging
import re
import threading
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

from . import memory as memory_mod

log = logging.getLogger("sky.learner")

REQUEST_TIMEOUT = 12  # seconds per network fetch
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SKY/1.0"

# RSS feeds the background learner pulls from. One dead feed never stops
# the others — failures are skipped silently.
RSS_FEEDS = [
    ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("BBC Technology", "https://feeds.bbci.co.uk/news/technology/rss.xml"),
    ("Google News (India)", "https://news.google.com/rss?hl=en-IN&gl=IN&ceid=IN:en"),
    ("Times of India Top", "https://timesofindia.indiatimes.com/rssfeedstopstories.cms"),
]

MAX_KNOWLEDGE_FACTS = 200  # rolling cap on "learned:" facts in the DB
_store_lock = threading.Lock()

_RESULT_RE = re.compile(
    r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    re.S | re.I,
)
_SNIPPET_RE = re.compile(
    r'class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>', re.S | re.I
)


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, d):
        self.parts.append(d)


def _strip_html(markup: str) -> str:
    p = _TextExtractor()
    try:
        p.feed(markup)
    except Exception:
        pass
    return re.sub(r"\s+", " ", " ".join(p.parts)).strip()


def _http_get(url: str, timeout: int = REQUEST_TIMEOUT) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(400_000)


def _feed_titles(url: str, limit: int = 8) -> list:
    """Titles from an RSS/Atom-ish feed (all our feeds are RSS 2.0)."""
    root = ET.fromstring(_http_get(url))
    titles = []
    for item in root.iter("item"):
        title = item.findtext("title")
        if title:
            titles.append(title.strip())
        if len(titles) >= limit:
            break
    return titles


def _mem(db_path: str = "data/memory.db"):
    """Fresh Memory connection — SQLite objects are per-thread."""
    return memory_mod.Memory(db_path)


def _add_knowledge(texts: list, source: str = "internet") -> int:
    """Insert texts as deduplicated 'learned:' general facts. Returns count."""
    with _store_lock:
        m = _mem()
        try:
            existing = {v.lower() for (_, _, v) in m.all_facts()}
            added = 0
            for t in texts:
                t = (t or "").strip()
                if not t or t.lower() in existing:
                    continue
                m.add_general(f"learned: {t}", cap=MAX_KNOWLEDGE_FACTS)
                existing.add(t.lower())
                added += 1
            return added
        finally:
            m.close()


def knowledge_count() -> int:
    m = _mem()
    try:
        return sum(1 for kind, _, v in m.all_facts()
                   if kind == "general" and v.startswith("learned:"))
    finally:
        m.close()


def learn_once() -> dict:
    """One round of background learning: pull fresh headlines from every
    feed into the knowledge store. Called by skyd.py on a timer."""
    fresh = []
    for source, url in RSS_FEEDS:
        try:
            for title in _feed_titles(url, limit=8):
                fresh.append(f"[{source}] {title}")
        except Exception:
            continue  # one dead feed shouldn't stop learning
    if not fresh:
        return {"ok": False, "new": 0, "total": knowledge_count(),
                "message": "Could not reach any news feed (offline?)."}
    added = _add_knowledge(fresh, source="internet")
    total = knowledge_count()
    log.info("learn_once: %d new, %d total", added, total)
    return {"ok": True, "new": added, "total": total,
            "message": f"Learned {added} new items ({total} in memory)."}


def digest(general_limit: int = 12, max_chars: int = 1400) -> str:
    """The newest things SKY has learned, formatted for the system prompt."""
    m = _mem()
    try:
        rows = m.conn.execute(
            "SELECT value FROM facts WHERE kind='general' AND value LIKE 'learned: %' "
            "ORDER BY id DESC LIMIT ?", (general_limit,)).fetchall()
    finally:
        m.close()
    out = ""
    for (text,) in rows:
        addition = "• " + text[len("learned: "):]
        if out and len(out) + len(addition) + 1 > max_chars:
            break
        out = (out + "\n" + addition) if out else addition
    return out


# ---------------------------------------------------------------------------
# Internet tools exposed to the model
# ---------------------------------------------------------------------------

def _clean_ddg_url(href: str) -> str:
    """DuckDuckGo wraps results in redirect links; unwrap to the real URL."""
    if "duckduckgo.com/l/?" in href:
        try:
            query = urllib.parse.urlsplit(
                "https:" + href if href.startswith("//") else href).query
            params = urllib.parse.parse_qs(query)
            if "uddg" in params:
                return urllib.parse.unquote(params["uddg"][0])
        except Exception:
            pass
    return href


def web_search(query: str, max_results: int = 5) -> dict:
    """Search the live web. Tries DuckDuckGo's HTML endpoint first; if it
    is blocked or empty, falls back to Google News' RSS search. Results
    are recorded into the knowledge store — browsing IS learning."""
    query = (query or "").strip()
    if not query:
        return {"ok": False, "message": "Empty search query."}

    results = []
    try:
        page = _http_get(
            "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
        ).decode("utf-8", "replace")
        titles = [re.sub(r"<[^>]+>", "", m.group(2)).strip()
                  for m in _RESULT_RE.finditer(page)]
        snippets = [_strip_html(m.group(1))[:180]
                    for m in _SNIPPET_RE.finditer(page)]
        for i, match in enumerate(_RESULT_RE.finditer(page)):
            if i >= max_results:
                break
            results.append({
                "title": titles[i],
                "url": _clean_ddg_url(match.group(1)),
                "snippet": snippets[i] if i < len(snippets) else "",
            })
    except Exception:
        results = []

    if not results:
        try:
            rss_url = ("https://news.google.com/rss/search?q="
                       + urllib.parse.quote(query)
                       + "&hl=en-IN&gl=IN&ceid=IN:en")
            results = [{"title": t, "url": "", "snippet": ""}
                       for t in _feed_titles(rss_url, limit=max_results)]
        except Exception:
            results = []

    if not results:
        return {"ok": False,
                "message": (f"No results for '{query}' — search may be "
                            "rate-limited right now; try again shortly.")}

    lines = [f"Top results for '{query}':"]
    remember = []
    for i, r in enumerate(results, 1):
        line = f"{i}. {r['title']}"
        if r["snippet"]:
            line += f" — {r['snippet']}"
        if r["url"]:
            line += f"  ({r['url']})"
        lines.append(line)
        note = f"[search '{query}'] {r['title']}"
        if r["snippet"]:
            note += f" — {r['snippet'][:140]}"
        remember.append(note)
    _add_knowledge(remember, source="search")
    return {"ok": True, "message": "\n".join(lines)}


def read_page(url: str, max_chars: int = 4000) -> dict:
    """Fetch a web page and return its readable text."""
    url = (url or "").strip()
    if not url:
        return {"ok": False, "message": "No URL given."}
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        raw = _http_get(url)
    except Exception as e:
        return {"ok": False, "message": f"Could not fetch {url}: {e}"}
    markup = raw.decode("utf-8", "replace")
    text = _strip_html(markup)
    if not text:
        return {"ok": False, "message": f"Fetched {url} but found no readable text."}
    note = f"[page] {url}: {text[:140]}"
    _add_knowledge([note], source="page")
    return {"ok": True, "message": f"Text of {url}:\n\n{text[:max_chars]}"}


if __name__ == "__main__":
    # Self-test:  python -m core.learner
    print(learn_once())
    print("--- digest ---")
    print(digest(8))
