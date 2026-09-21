#!/usr/bin/env python3
"""P7 HUD: SKY terminal dashboard (Textual) — the Electron replacement.

Run:  .venv/bin/python sky_hud.py        (or sky.py --hud)

Live status (CPU/RAM/disk, brain reachability, skyd daemon), mic RMS meter,
chat log + input box. Input turns run the FULL agent; non-read-only commands
are auto-denied here (use the REPL `sky.py` for typed confirm gates).
"""
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from textual.app import App, ComposeResult  # noqa: E402
from textual.containers import Grid  # noqa: E402
from textual.widgets import Footer, Header, Input, RichLog, Static  # noqa: E402

import sky as sky_core  # noqa: E402  (reuse load_cfg/agent_turn/speak helpers)
from core import memory  # noqa: E402

try:
    from textual.widgets import Sparkline
except ImportError:  # very old textual
    Sparkline = None


class SkyHud(App):
    TITLE = "SKY console"
    BINDINGS = [("ctrl+q", "quit", "Quit")]
    CSS = """
    #grid { grid-size: 2 2; grid-columns: 32 1fr; grid-rows: 1fr 6; height: 100%; }
    #status { border: round green; padding: 0 1; }
    #chatlog { border: round $accent; }
    #micbox { border: round cyan; padding: 0 1; }
    #cmd { border: tall $panel; }
    """

    def __init__(self, cfg, mem):
        super().__init__()
        self.cfg = cfg
        self.mem = mem
        self.replies = []       # test hook
        self.last_status = ""   # test hook
        self._rms = []
        self._mic_on = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Grid(id="grid"):
            yield Static("", id="status")
            yield RichLog(id="chatlog", markup=True, wrap=True, highlight=False)
            with Grid(id="micbox"):
                yield Static("mic: starting…", id="miclabel")
                if Sparkline is not None:
                    yield Sparkline([], id="micspark")
            yield Input(placeholder="talk to sky…  (ctrl+q quit)", id="cmd")
        yield Footer()

    # ---------- status ----------

    def _brain_up(self) -> bool:
        u = urlparse(self.cfg.get("base_url", ""))
        host, port = u.hostname or "localhost", u.port or 80
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            return False

    def _skyd_up(self) -> bool:
        try:
            return subprocess.run(["pgrep", "-f", "skyd.py"],
                                  stdout=subprocess.DEVNULL).returncode == 0
        except Exception:
            return False

    def _refresh_status(self) -> None:
        if not self.is_running:
            return
        try:
            import psutil
            cpu = psutil.cpu_percent(interval=None)
            ram = psutil.virtual_memory().percent
            disk = psutil.disk_usage("/").percent
            self.last_status = "\n".join([
                f"brain  : {'UP' if self._brain_up() else 'DOWN'}  "
                f"({self.cfg.get('model', '?')})",
                f"skyd   : {'running' if self._skyd_up() else 'NOT running'}",
                f"cpu    : {cpu:.0f}%",
                f"ram    : {ram:.0f}%",
                f"disk   : {disk:.0f}%",
            ])
        except Exception:
            self.last_status = "(status unavailable)"
        try:
            self.query_one("#status", Static).update(self.last_status)
        except Exception:
            pass

    # ---------- mic meter ----------

    def _start_mic(self) -> None:
        try:
            import numpy as np
            import sounddevice as sd
            if not any(d.get("max_input_channels", 0) > 0 for d in sd.query_devices()):
                raise RuntimeError("no mic")

            def cb(indata, frames, t, status):
                self._rms.append(float(np.sqrt(np.mean(indata * indata))))
                if len(self._rms) > 600:
                    del self._rms[:300]

            self._stream = sd.InputStream(samplerate=16000, channels=1,
                                          dtype="float32", blocksize=1600,
                                          callback=cb)
            self._stream.start()
            self._mic_on = True
        except Exception:
            self._mic_on = False

    def _refresh_mic(self) -> None:
        if not self.is_running:
            return
        try:
            lbl = self.query_one("#miclabel", Static)
            if not self._mic_on:
                lbl.update("mic: OFF (no device)")
                return
            recent = self._rms[-40:]
            lvl = max(recent) if recent else 0.0
            lbl.update(f"mic: live  peak {lvl:.3f}")
            if Sparkline is not None:
                try:
                    self.query_one("#micspark", Sparkline).data = recent
                except Exception:
                    pass
        except Exception:
            pass

    # ---------- chat ----------

    def on_mount(self) -> None:
        log = self.query_one("#chatlog", RichLog)
        log.write(f"[b]SKY HUD online[/b] — {time.strftime('%Y-%m-%d %H:%M')}")
        log.write("type below; read-only commands run free, write-commands are")
        log.write("auto-denied here (use sky.py REPL for confirm gates).")
        self._start_mic()
        self._refresh_status()
        self.set_interval(2.0, self._refresh_status)
        self.set_interval(0.5, self._refresh_mic)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.clear()
        if not text:
            return
        self.query_one("#chatlog", RichLog).write(f"[b]you>[/b] {text}")
        threading.Thread(target=self._turn, args=(text,), daemon=True).start()

    def _turn(self, text: str) -> None:
        mem = memory.Memory(ROOT / "data" / "memory.db")  # sqlite per thread
        try:
            reply = sky_core.agent_turn(self.cfg, mem, text, confirm_fn=None)
            self.replies.append(reply)
            self.call_from_thread(self._log_reply, reply)
            sky_core.speak_if_enabled(self.cfg, reply)
        except sky_core.llm.LLMDown as e:
            self.call_from_thread(self._log_reply, f"(offline) brain unreachable — {e}")
        except Exception as e:
            self.call_from_thread(self._log_reply, f"(error) {e}")
        finally:
            mem.close()

    def _log_reply(self, text: str) -> None:
        self.query_one("#chatlog", RichLog).write(f"sky> {text}")


def run(cfg, mem):
    SkyHud(cfg, mem).run()


if __name__ == "__main__":
    from core import llm  # noqa: F401  (import for parity with sky.py)
    run(sky_core.load_cfg(), memory.Memory(ROOT / "data" / "memory.db"))
