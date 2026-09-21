"""30s smoke test: does pywebview open a native WebView2 window on this box?"""
import threading
import time

import webview

html = """<!doctype html><html><body style="margin:0;background:#04121c;
color:#22d3ee;font-family:Consolas;display:flex;align-items:center;
justify-content:center;height:100vh"><h1>SKY IRON SMOKE OK</h1>
</body></html>"""

w = None

def killer():
    time.sleep(12)
    try:
        w.destroy()
    except Exception:
        pass

w = webview.create_window("SKY-IRON-SMOKE", html=html, width=800, height=560)
threading.Thread(target=killer, daemon=True).start()
webview.start()
print("SMOKE WINDOW CLOSED CLEANLY")
