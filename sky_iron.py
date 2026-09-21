#!/usr/bin/env python3
"""SKY IRON — the J.A.R.V.I.S.-class native interface. No browser.

Fullscreen WebView2 window rendering the IRON HUD (hub/iron.html).
Ensures the hub backend (hub/server.py) is up first; the HUD talks to it
over 127.0.0.1 exactly like every other Sky module. Single-instance:
a second launch exits silently.

Run:   .venv/Scripts/pythonw.exe sky_iron.py   (autostart / silent)
Debug: .venv/Scripts/python.exe   sky_iron.py
Keys:  ESC quits the window, F11 toggles fullscreen.
"""
import argparse
import ctypes
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import webview

ROOT = Path(__file__).resolve().parent
HUB_URL = "http://127.0.0.1:20130/iron.html"
HEALTH = "http://127.0.0.1:20130/api/health"
LOCK_PORT = 20131  # legacy; single-instance now via Win32 mutex in main()

RETRY_HTML = """<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="2;url=%s">
<style>body{background:#020609;color:#22d3ee;font-family:Consolas;
display:grid;place-items:center;height:100vh;margin:0}</style></head>
<body><div><h2>SKY OS</h2><p>linking hub backend…</p></div></body></html>"""


def hub_alive() -> bool:
    try:
        urllib.request.urlopen(HEALTH, timeout=1.5).read(16)
        return True
    except Exception:
        return False


def ensure_hub() -> bool:
    if hub_alive():
        return True
    pyw = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    if not pyw.is_file():
        pyw = ROOT / ".venv" / "Scripts" / "python.exe"
    try:
        subprocess.Popen([str(pyw), str(ROOT / "hub" / "server.py")],
                         cwd=str(ROOT), creationflags=0x00000008)  # DETACHED_PROCESS
    except Exception:
        return False
    for _ in range(48):
        if hub_alive():
            return True
        time.sleep(0.25)
    return False


class Api:
    """Exposed to JS as window.pywebview.api."""

    def quit(self):
        for w in webview.windows:
            w.destroy()

    def toggle_fs(self):
        for w in webview.windows:
            w.toggle_fullscreen()

    def come_here(self):
        """Mouse cursor screen position (physical px) — 'idhar aao' target."""
        try:
            import ctypes

            class Pt(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

            p = Pt()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
            return {"x": p.x, "y": p.y}
        except Exception:
            return {"x": -1, "y": -1}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quit", action="store_true",
                    help="no-op placeholder for symmetry with sky_app.py")
    args = ap.parse_args()
    if args.quit:
        return 0

    # single instance (Win32 mutex — TCP port locks break when WSL's
    # wslrelay relays omniroute's listeners onto Windows localhost;
    # omniroute now listens on 20131/20132 inside WSL)
    ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\SKY_IRON_SINGLETON")
    if ctypes.windll.kernel32.GetLastError() == 183:
        return 0  # IRON HUD already running

    ok = ensure_hub()
    win = webview.create_window(
        "SKY · NEURAL OS",
        url=HUB_URL if ok else None,
        html=None if ok else RETRY_HTML % HUB_URL,
        js_api=Api(),
        fullscreen=True,
        frameless=True,
        background_color="#020609",
    )
    webview.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
