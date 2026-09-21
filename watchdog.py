#!/usr/bin/env python3
"""SKY watchdog — keeps Sky's always-on parts alive.

Task Scheduler runs this every 5 minutes (pythonw.exe, hidden). For each
component (skyd, skyear, hub web UI) it checks whether a python process with
that script in its command line exists; if one died it is relaunched detached
and hidden. Never starts a second copy of anything that is already running.

Manual run:  .venv\\Scripts\\python.exe watchdog.py
"""
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = ROOT / ".venv" / "Scripts" / "python.exe"
PYW = ROOT / ".venv" / "Scripts" / "pythonw.exe"
LOG = ROOT / "logs" / "watchdog.log"
DETACHED = 0x00000008 | 0x08000000  # DETACHED_PROCESS | CREATE_NO_WINDOW

COMPONENTS = [
    # name, command-line fragment (lowercase), launch args
    ("skyd", "skyd.py", [PY, ROOT / "skyd.py"]),
    ("skyear", "skyear.py", [PY, ROOT / "skyear.py"]),
    ("hub", "hub\\server.py", [PYW, ROOT / "hub" / "server.py"]),
]


def _cmdlines() -> str:
    """All python/pythonw command lines on this machine, lowercase.

    WMI transiently returns nothing under load; an empty result here would
    make the caller respawn EVERYTHING (duplicate daemons). So retry once,
    and on persistent failure return "" which main() treats as 'blind'.
    """
    for _ in range(2):
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_Process -Filter \"Name='python.exe' or "
                 "Name='pythonw.exe'\").CommandLine"],
                capture_output=True, text=True, timeout=45,
                creationflags=0x08000000,
            )
            txt = (out.stdout or "").lower()
            if txt.strip():
                return txt
        except Exception:
            pass
        time.sleep(2)
    return ""


def _spawn(args):
    subprocess.Popen(
        [str(a) for a in args], cwd=str(ROOT),
        creationflags=DETACHED,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )


def main() -> int:
    LOG.parent.mkdir(exist_ok=True)
    venv = str(ROOT / ".venv").lower()
    cl = _cmdlines()
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    if not cl.strip():
        # WMI blind — cannot prove what's running; spawning now risks duplicates
        try:
            with LOG.open("a", encoding="utf-8") as f:
                f.write(f"{stamp} | detection blind — skipped this cycle\n")
        except Exception:
            pass
        return 0
    restarted = []
    for name, frag, args in COMPONENTS:
        # fragment match on the whole command line; a second Sky install on
        # one machine is unsupported (docs say one install per PC)
        hit = frag in cl
        if not hit:
            _spawn(args)
            restarted.append(name)
    state = ("restarted: " + ", ".join(restarted)) if restarted else "all up"
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(f"{stamp} | {state}\n")
    except Exception:
        pass
    if restarted and len(sys.argv) > 1 and sys.argv[1] == "--print":
        print(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
