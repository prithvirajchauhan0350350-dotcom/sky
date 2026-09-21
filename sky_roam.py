#!/usr/bin/env python3
"""SKY ROAM — the roaming companion window.

A small, frameless, transparent, always-on-top WebView2 pod that renders the
Live2D girl (hub/roam.html). She wanders the desktop freely, talks through the
real Sky brain (hub /api/chat -> sky.agent_turn with full system tools), hears
you via the mic, speaks with Sky's voice, and shows human emotions via
hub/skyface.js. Drag her anywhere; hover/input pauses the wandering.

Run:   .venv/Scripts/pythonw.exe sky_roam.py   (autostart / silent)
Debug: .venv/Scripts/python.exe   sky_roam.py
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
ROAM_URL = "http://127.0.0.1:20130/roam.html"
HEALTH = "http://127.0.0.1:20130/api/health"
MUTEX = "Local\\SKY_ROAM_SINGLETON"


def already_running() -> bool:
    """Single-instance guard via Win32 named mutex.

    The old TCP-port lock (127.0.0.1:20132) broke silently: WSL's wslrelay.exe
    relays WSL-side listeners onto Windows localhost, and the omniroute gateway
    now listens on 20131/20132 inside WSL — so the bind always failed and the
    pod exited before its window appeared. A named mutex can't be stolen.
    """
    ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX)
    return ctypes.windll.kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS

RETRY_HTML = """<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="2;url=%s">
<style>body{background:transparent;color:#22d3ee;font-family:Consolas;
display:grid;place-items:center;height:100vh;margin:0}</style></head>
<body><div>SKY · linking hub…</div></body></html>"""


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
                         cwd=str(ROOT), creationflags=0x00000008)
    except Exception:
        return False
    for _ in range(48):
        if hub_alive():
            return True
        time.sleep(0.25)
    return False


class Api:
    """Exposed to JS as window.pywebview.api."""

    def _win(self):
        return webview.windows[0] if webview.windows else None

    def move_to(self, x, y):
        w = self._win()
        if w is None:
            return
        try:
            w.move(int(x), int(y))
        except Exception:
            pass

    def quit(self):
        for w in webview.windows:
            w.destroy()

    def open_hud(self):
        """Open the fullscreen SKY OS HUD (sky_iron.py) beside the pod."""
        pyw = ROOT / ".venv" / "Scripts" / "pythonw.exe"
        if not pyw.is_file():
            pyw = ROOT / ".venv" / "Scripts" / "python.exe"
        try:
            subprocess.Popen([str(pyw), str(ROOT / "sky_iron.py")],
                             cwd=str(ROOT), creationflags=0x00000008)
        except Exception:
            pass

    def come_here(self):
        """Screen coordinates of the mouse cursor — 'idhar aao' target."""
        try:
            import ctypes
            import ctypes.wintypes
            pt = ctypes.wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            return [int(pt.x), int(pt.y)]
        except Exception:
            return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quit", action="store_true",
                    help="no-op placeholder, symmetry with sky_iron.py")
    args = ap.parse_args()
    if args.quit:
        return 0

    # single instance (Win32 mutex — TCP port locks break when WSL's
    # wslrelay relays omniroute's listeners onto Windows localhost)
    if already_running():
        return 0  # SKY ROAM already running

    ok = ensure_hub()
    webview.create_window(
        "SKY",
        url=ROAM_URL if ok else None,
        html=None if ok else RETRY_HTML % ROAM_URL,
        js_api=Api(),
        width=350,
        height=500,          # keep in sync with roam.html clampToScreen()
        frameless=True,
        transparent=True,
        on_top=True,
        easy_drag=False,
        shadow=False,
        background_color="#000000",
    )
    webview.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
