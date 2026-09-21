#!/usr/bin/env python3
"""SKY daemon (P5): reminders + daily briefing. Run: .venv/bin/python skyd.py

- polls the reminders table every 20s; fires due ones (speaks when audio works)
- daily briefing at config.briefing_time (default 09:00)
- on start, reports reminders missed while it was off
"""
import json
import logging
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from apscheduler.schedulers.background import BackgroundScheduler  # noqa: E402

from core import telemetry, voice  # noqa: E402
from core.learner import learn_once  # noqa: E402
from core.memory import Memory  # noqa: E402

log = logging.getLogger("skyd")


def setup_logging():
    h = RotatingFileHandler(ROOT / "logs" / "skyd.log", maxBytes=1_000_000, backupCount=3)
    h.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[h])


def say(mem, text, try_speak=True):
    print("sky>", text, flush=True)
    log.info("SAY: %s", text)
    if try_speak:
        try:
            voice.speak(text)
        except Exception as e:
            log.info("speech unavailable (%s) — text only", str(e)[:80])


def fire_due(mem, missed_only=False):
    now = time.time()
    due = mem.due_reminders(now)
    if not due:
        return
    ids, texts = [], []
    for rid, text, due_ts in due:
        when = time.strftime("%H:%M", time.localtime(due_ts))
        prefix = "MISSED reminder (I was off) from" if missed_only else "Reminder, sir"
        texts.append(f"{prefix} {when}: {text}" if missed_only else f"{text}")
        ids.append(rid)
    mem.mark_fired(ids)
    say(mem, " . ".join(texts))


def briefing(mem, try_speak=True):
    lines = telemetry.briefing_lines()
    pend = mem.pending_reminders()
    if pend:
        nxt = pend[0]
        lines.append(f"Next reminder at {time.strftime('%H:%M', time.localtime(nxt[2]))}: {nxt[1]}")
    say(mem, "Good day, sir. " + " ".join(lines), try_speak)


def main():
    from core import single_instance
    if not single_instance("Local\\SKY_SKYD_SINGLETON"):
        logging.getLogger("skyd").info("skyd already running — exiting quietly")
        return
    setup_logging()
    log = logging.getLogger("skyd")
    cfg = json.loads((ROOT / "config.json").read_text())
    mem = Memory(ROOT / "data" / "memory.db")

    # missed reminders from any downtime
    missed = mem.due_reminders()
    if missed:
        fire_due(mem, missed_only=True)
    else:
        say(mem, "Sky daemon online, sir.", try_speak=False)

    sched = BackgroundScheduler()
    sched.add_job(fire_due, "interval", args=[mem], seconds=20, id="reminders")
    h, m = (cfg.get("briefing_time") or "09:00").split(":")[:2]
    sched.add_job(briefing, "cron", args=[mem], hour=int(h), minute=int(m), id="briefing")
    # background learning: pull fresh headlines into knowledge every 90 min
    sched.add_job(learn_once, "interval", minutes=90,
                  id="learning", max_instances=1, coalesce=True, misfire_grace_time=600)
    sched.start()
    log.info("skyd up: reminders every 20s, briefing at %02d:%02d, learning every 90m",
             int(h), int(m))
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        sched.shutdown()
        log.info("skyd stopped")


if __name__ == "__main__":
    main()
