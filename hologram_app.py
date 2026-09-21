#!/usr/bin/env python3
"""SKY hologram: a small animated wireframe head on the desktop (bottom-
right) whose mouth follows whatever SKY says.

Run (Windows):  .venv\\Scripts\\python.exe hologram_app.py
   (or just ask SKY — the `toggle_hologram` tool starts/stops this app)

Control API, localhost only (127.0.0.1:20129):
    GET  /ping            -> 200 OK if this app is alive
    POST /say  text=...   -> speak the text with lip sync (queued, max 3)
    POST /hide            -> hide the head (speech keeps playing)
    POST /show            -> show the head again
    POST /quit            -> exit this app

CLI helpers:  hologram_app.py --say "hello"   |   hologram_app.py --quit
"""
import logging
import queue
import sys
import threading
import time
import tkinter as tk
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "win"))  # WSL dev layout keeps modules under win/

from spy_hologram import Hologram  # noqa: E402
from spy_avatar import (  # noqa: E402
    build_viseme_timeline, play_animated, synthesize_with_timing)

PORT = 20129
TMP = Path(__file__).resolve().parent / "data" / "hologram"
RATE = "+8%"  # matches core/voice.py speaking rate

log = logging.getLogger("holo")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s holo %(levelname)s %(message)s")

HEAD = None      # set in main(): the Hologram instance
HTTPD = None     # set in main(): the control server


def _play_plain(path: str):
    """Fallback: play the mp3 without mouth animation."""
    try:
        from core.voice import _play_mp3_windows
        _play_mp3_windows(path)
    except Exception as e:
        log.warning("plain playback failed: %s", e)


def _speak_once(text: str):
    """Synthesize + play one line, animating the mouth in real time."""
    from core.voice import pick_voice
    TMP.mkdir(parents=True, exist_ok=True)
    mp3 = TMP / f"holo_{int(time.time() * 1000)}.mp3"
    try:
        path, words = synthesize_with_timing(text, pick_voice(text),
                                             str(mp3), rate=RATE)
        if not path:
            log.warning("synthesis failed; line dropped: %.60s", text)
            return
        frames = build_viseme_timeline(words)
        if not play_animated(str(path), frames, HEAD.set_mouth):
            _play_plain(str(path))  # no viseme track / MCI refused — audio only
    except Exception as e:
        log.warning("speech job failed: %s", e)
    finally:
        try:
            mp3.unlink(missing_ok=True)
        except OSError:
            pass


def _speech_worker():
    """One line at a time: speaking state -> play+animate -> idle."""
    while True:
        text = SPEECH_Q.get()
        HEAD.set_state("speaking")
        try:
            _speak_once(text)
        finally:
            HEAD.set_state("idle")


SPEECH_Q: "queue.Queue[str]" = queue.Queue(maxsize=3)


class _Ctl(BaseHTTPRequestHandler):
    """Localhost control server. Requests come only from SKY itself."""

    def log_message(self, *args):  # silence per-request stderr spam
        pass

    # -- helpers ----------------------------------------------------------
    def _reply(self, code: int, text: str):
        data = text.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _form(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return urllib.parse.parse_qs(self.rfile.read(n).decode("utf-8", "replace"))

    # -- routes -----------------------------------------------------------
    def do_GET(self):
        if self.path == "/ping":
            self._reply(200, "OK")
        else:
            self._reply(404, "ERR unknown")

    def do_POST(self):
        form = self._form()
        if self.path == "/say":
            text = (form.get("text") or [""])[0].strip()
            if not text:
                self._reply(400, "ERR empty text")
            elif HEAD is None:
                self._reply(503, "ERR head not ready")
            else:
                try:
                    SPEECH_Q.put_nowait(text[:1000])
                    self._reply(200, "OK")
                except queue.Full:
                    self._reply(503, "BUSY")
        elif self.path == "/hide":
            HEAD.hide()
            self._reply(200, "OK")
        elif self.path == "/show":
            HEAD.show()
            self._reply(200, "OK")
        elif self.path == "/quit":
            self._reply(200, "OK")
            HTTPD.shutdown()
            HEAD.root.after(0, HEAD.root.destroy)  # marshal onto the Tk thread
        else:
            self._reply(404, "ERR unknown")


def _post(path: str, form: dict) -> str:
    """Client helper for the CLI below."""
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}",
                                     data=urllib.parse.urlencode(form).encode())
        with urllib.request.urlopen(req, timeout=5) as r:
            return f"{r.status} {r.read(200).decode(errors='replace')}"
    except Exception as e:
        return f"ERR {e}"


def main() -> int:
    global HEAD, HTTPD
    root = tk.Tk()
    root.withdraw()
    HEAD = Hologram(root)
    HEAD.show()
    try:
        HTTPD = ThreadingHTTPServer(("127.0.0.1", PORT), _Ctl)
    except OSError as e:
        print(f"cannot open control port {PORT}: {e} "
              f"— is hologram_app.py already running?")
        return 1
    HTTPD.daemon_threads = True
    threading.Thread(target=HTTPD.serve_forever, daemon=True).start()
    threading.Thread(target=_speech_worker, daemon=True).start()
    print(f"SKY hologram online (control: http://127.0.0.1:{PORT})")
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    if "--say" in sys.argv[1:]:
        i = sys.argv.index("--say")
        print(_post("/say", {"text": " ".join(sys.argv[i + 1:]) or "Hello, sir."}))
    elif "--quit" in sys.argv[1:]:
        print(_post("/quit", {}))
    elif "--hide" in sys.argv[1:]:
        print(_post("/hide", {}))
    elif "--show" in sys.argv[1:]:
        print(_post("/show", {}))
    else:
        time.sleep(0.2)  # let fonts/sockets settle before Tk takes over
        sys.exit(main())
