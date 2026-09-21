"""Live system telemetry (psutil). Used by agent tool `sys_report` and briefings.

Windows-native: boot/disk probes use C:\\.
"""
import logging
import platform
import time

import psutil

log = logging.getLogger("sky.telemetry")


def _gb(n):
    return f"{n / (1024 ** 3):.1f} GB"


def snapshot() -> dict:
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("C:\\")
    return {
        "cpu_percent": psutil.cpu_percent(interval=0.5),
        "ram_used": _gb(mem.used),
        "ram_total": _gb(mem.total),
        "ram_percent": mem.percent,
        "disk_used": _gb(disk.used),
        "disk_total": _gb(disk.total),
        "disk_percent": disk.percent,
        "uptime_h": round((time.time() - psutil.boot_time()) / 3600, 1),
    }


def report() -> str:
    s = snapshot()
    top = []
    for p in sorted(psutil.process_iter(["name", "memory_info"]),
                    key=lambda x: x.info.get("memory_info", type("m", (), {"rss": 0})()).rss
                    if x.info.get("memory_info") else 0,
                    reverse=True)[:3]:
        try:
            top.append(f"{p.info['name']} ({_gb(p.info['memory_info'].rss)})")
        except Exception:
            continue
    lines = [
        f"CPU {s['cpu_percent']}% | RAM {s['ram_used']}/{s['ram_total']} ({s['ram_percent']}%)",
        f"Disk C: {s['disk_used']}/{s['disk_total']} ({s['disk_percent']}%) "
        f"| uptime {s['uptime_h']}h",
        f"Top memory: {', '.join(top)}",
    ]
    return "\n".join(lines)


def briefing_lines() -> list:
    s = snapshot()
    out = [f"Time is {time.strftime('%H:%M')}.",
           f"CPU at {s['cpu_percent']} percent, RAM {s['ram_percent']} percent.",
           f"C: drive at {s['disk_percent']} percent."]
    if s["disk_percent"] > 85 or s["ram_percent"] > 85:
        out.append("Attention required, sir — usage is high.")
    return out
