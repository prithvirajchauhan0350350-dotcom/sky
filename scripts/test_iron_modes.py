"""Bisect pywebview window options: which flag kills the window?
Usage: python test_iron_modes.py <mode> [full] [frameless] [api]
"""
import sys
import threading

import webview

mode = sys.argv[1] if len(sys.argv) > 1 else "plain"
full = "full" in sys.argv
frameless = "frameless" in sys.argv
use_api = "api" in sys.argv


class Api:
    def quit(self):
        for w in webview.windows:
            w.destroy()

    def toggle_fs(self):
        for w in webview.windows:
            w.toggle_fullscreen()


w = webview.create_window(
    "SKY-IRON-TEST-" + mode,
    "http://127.0.0.1:20130/iron.html",
    js_api=Api() if use_api else None,
    fullscreen=full,
    frameless=frameless,
    background_color="#020609",
)
threading.Timer(18, lambda: [x.destroy() for x in webview.windows]).start()
webview.start()
print("EXIT-CLEAN " + mode)
