"""
spy_vitals.py — Situational awareness & diagnostics for Spy (JARVIS mode).
--------------------------------------------------------------------------
Reads the "biometrics" of the user's machine: CPU load, RAM, battery/AC
power, disk space, uptime, internet reachability, and the focused window.

Used three ways by spy.py:
  * get_vitals()   — the system_vitals tool (live machine status)
  * diagnose()     — the run_diagnostics tool (full formatted sweep)
  * situational_line() — injected into every system prompt so Spy always
    knows the time, the focused window, and the machine's state
  * collect_alerts() — proactive warnings (low battery, RAM pressure, low
    disk, internet down) for the background monitor thread

Windows APIs via ctypes (battery, RAM, uptime); psutil optional for CPU
percent. Results are cached briefly so prompt-building stays fast.
"""

import ctypes
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime

try:
    import psutil  # optional — only used for CPU percent
except ImportError:
    psutil = None

_cache = {"ts": 0.0, "data": None}
_CACHE_SECONDS = 20
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# Raw readings
# ---------------------------------------------------------------------------

def _battery() -> dict:
    """{'pct': 87 or None, 'on_ac': bool or None} via GetSystemPowerStatus."""
    try:
        class SPS(ctypes.Structure):
            _fields_ = [
                ("ACLineStatus", ctypes.c_byte),
                ("BatteryFlag", ctypes.c_byte),
                ("BatteryLifePercent", ctypes.c_byte),
                ("Reserved0", ctypes.c_byte),
                ("BatteryLifeTime", ctypes.c_ulong),
                ("BatteryFullLifeTime", ctypes.c_ulong),
            ]
        sps = SPS()
        if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(sps)):
            pct = sps.BatteryLifePercent
            has_battery = not (sps.BatteryFlag & 128) and pct != 255
            return {
                "pct": pct if has_battery else None,
                "on_ac": bool(sps.ACLineStatus),
            }
    except Exception:
        pass
    return {"pct": None, "on_ac": None}


def _ram() -> dict:
    """{'pct': used %, 'total_gb': float, 'free_gb': float}."""
    try:
        class MS_EX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        ms = MS_EX()
        ms.dwLength = ctypes.sizeof(MS_EX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
            total = ms.ullTotalPhys / 1024 ** 3
            free = ms.ullAvailPhys / 1024 ** 3
            return {"pct": int(ms.dwMemoryLoad), "total_gb": round(total, 1),
                    "free_gb": round(free, 1)}
    except Exception:
        pass
    return {"pct": None, "total_gb": None, "free_gb": None}


def _uptime_hours() -> float | None:
    try:
        ms = ctypes.windll.kernel32.GetTickCount64()
        return round(ms / 1000 / 3600, 1)
    except Exception:
        return None


def _internet() -> str:
    for host, port in (("1.1.1.1", 53), ("8.8.8.8", 53)):
        try:
            socket.create_connection((host, port), timeout=1.5).close()
            return "online"
        except OSError:
            continue
    return "offline"


def _cpu_pct() -> str:
    if psutil is not None:
        try:
            return f"{int(psutil.cpu_percent(interval=0.3))}%"
        except Exception:
            pass
    return "n/a (pip install psutil for live CPU load)"


def _disk() -> dict:
    drive = os.environ.get("SystemDrive", "C:") + "\\"
    try:
        usage = shutil.disk_usage(drive)
        return {"free_gb": round(usage.free / 1024 ** 3, 1),
                "total_gb": round(usage.total / 1024 ** 3, 1)}
    except Exception:
        return {"free_gb": None, "total_gb": None}


def top_ram(n: int = 5) -> list:
    """Top-n processes grouped by name, by total RAM (GB). psutil optional."""
    if psutil is None:
        return []
    agg = {}
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            name = (p.info["name"] or "?").split(".exe")[0]
            rss = p.info["memory_info"].rss or 0
            agg[name] = agg.get(name, 0) + rss
        except Exception:
            continue
    best = sorted(agg.items(), key=lambda kv: kv[1], reverse=True)[: max(1, int(n))]
    return [{"name": k, "ram_gb": round(v / 1024 ** 3, 2)} for k, v in best]


def _active_window() -> str:
    try:
        import automation
        return automation.active_window_title() or "(unknown)"
    except Exception:
        return "(unknown)"


def vitals_raw() -> dict:
    """All readings in one dict, cached for _CACHE_SECONDS."""
    now = time.time()
    if _cache["data"] is not None and now - _cache["ts"] < _CACHE_SECONDS:
        return _cache["data"]
    data = {
        "time": datetime.now().strftime("%A, %d %B %Y, %H:%M"),
        "os": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
        "uptime_h": _uptime_hours(),
        "battery": _battery(),
        "ram": _ram(),
        "disk": _disk(),
        "cpu": _cpu_pct(),
        "internet": _internet(),
        "active_window": _active_window(),
        "cores": os.cpu_count(),
        "top_ram": top_ram(5),
    }
    _cache["ts"] = now
    _cache["data"] = data
    return data


# ---------------------------------------------------------------------------
# Formatted outputs
# ---------------------------------------------------------------------------

def _battery_text(b: dict) -> str:
    if b["pct"] is None:
        return "AC power (no battery detected)"
    state = "charging" if b["on_ac"] else "on battery"
    return f"{b['pct']}% ({state})"


def get_vitals() -> dict:
    """The system_vitals tool: a compact live status of the machine."""
    v = vitals_raw()
    lines = [
        f"Time: {v['time']}",
        f"System: {v['os']} | Python {v['python']} | {v['cores']} cores | "
        f"CPU load {v['cpu']}",
        f"RAM: {v['ram']['pct']}% used "
        f"({v['ram']['free_gb']} GB free of {v['ram']['total_gb']} GB)",
        "Top RAM: " + (", ".join(f"{p['name']} {p['ram_gb']}GB"
                                 for p in v.get("top_ram") or []) or "n/a"),
        f"Battery: {_battery_text(v['battery'])}",
        f"Disk {os.environ.get('SystemDrive', 'C:')}: {v['disk']['free_gb']} GB free "
        f"of {v['disk']['total_gb']} GB",
        f"Uptime: {v['uptime_h']} h",
        f"Internet: {v['internet']}",
        f"Focused window: {v['active_window']}",
    ]
    return {"ok": True, "message": "\n".join(lines)}


def diagnose(learner_items: int = 0) -> dict:
    """The run_diagnostics tool: a full JARVIS-style sweep."""
    v = vitals_raw()
    problems = []
    if v["battery"]["pct"] is not None and not v["battery"]["on_ac"] and v["battery"]["pct"] <= 20:
        problems.append("battery critically low")
    if (v["ram"]["pct"] or 0) >= 90:
        problems.append("RAM under heavy pressure")
    if (v["disk"]["free_gb"] or 999) <= 10:
        problems.append("disk nearly full")
    if v["internet"] == "offline":
        problems.append("no internet — learning and search disabled")

    lines = [
        "FULL SYSTEM DIAGNOSTIC",
        "----------------------",
        f"OS: {v['os']} | Python {v['python']}",
        f"Uptime: {v['uptime_h']} h | Cores: {v['cores']} | CPU load: {v['cpu']}",
        f"RAM: {v['ram']['pct']}% used ({v['ram']['free_gb']} of {v['ram']['total_gb']} GB free)",
        f"Battery: {_battery_text(v['battery'])}",
        f"Disk {os.environ.get('SystemDrive', 'C:')}: {v['disk']['free_gb']} of "
        f"{v['disk']['total_gb']} GB free",
        f"Internet: {v['internet']}",
        f"Focused window: {v['active_window']}",
        f"Memory database: {learner_items} learned items",
    ]
    if problems:
        lines.append("Issues detected: " + "; ".join(problems))
        lines.append("Recommendation: let me handle it — tell me what to do.")
    else:
        lines.append("All systems nominal, sir.")
    return {"ok": True, "message": "\n".join(lines)}


def situational_line() -> str:
    """Compact awareness block injected into every system prompt."""
    v = vitals_raw()
    return (
        f"Local time: {v['time']}\n"
        f"Focused window: {v['active_window']}\n"
        f"Machine: CPU {v['cpu']} | RAM {v['ram']['pct']}% | "
        f"Battery: {_battery_text(v['battery'])} | Internet: {v['internet']}"
    )


# ---------------------------------------------------------------------------
# Proactive alerts (suppressed for 30 min per condition)
# ---------------------------------------------------------------------------

ALERT_REPEAT_SECONDS = 1800
_last_alerts: dict = {}


def collect_alerts() -> list:
    """New proactive warnings since the last call. Each condition fires at
    most once every ALERT_REPEAT_SECONDS."""
    v = vitals_raw()
    alerts = []

    def fire(key, msg):
        if time.time() - _last_alerts.get(key, 0) > ALERT_REPEAT_SECONDS:
            _last_alerts[key] = time.time()
            alerts.append((key, msg))

    b = v["battery"]
    if b["pct"] is not None and not b["on_ac"] and b["pct"] <= 20:
        fire("battery", f"Sir, battery at {b['pct']}% — I suggest plugging in before it dies mid-task.")
    if (v["ram"]["pct"] or 0) >= 92:
        fire("ram", f"RAM is at {v['ram']['pct']}% — the machine is gasping. Say the word and I'll close things.")
    if (v["disk"]["free_gb"] or 999) <= 10:
        fire("disk", f"Only {v['disk']['free_gb']} GB free on {os.environ.get('SystemDrive', 'C:')}: — that will start hurting soon.")
    if v["internet"] == "offline":
        fire("net", "Internet is down. I can still run the machine, but no learning until it's back.")
    return alerts


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(get_vitals()["message"])
    print()
    print(diagnose(learner_items=112)["message"])
    print()
    print("--- situational line ---")
    print(situational_line())
    print("--- alerts (first pass) ---")
    print(collect_alerts())
