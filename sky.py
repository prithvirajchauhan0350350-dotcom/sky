#!/usr/bin/env python3
"""SKY - personal AI assistant (JARVIS-style). All phases wired.

Run modes:
  .venv/bin/python sky.py                 interactive agent REPL (hands on)
  .venv/bin/python sky.py --ask "..."     one-shot agentic answer
  .venv/bin/python sky.py --talk          voice mode (push-to-talk)
  .venv\\Scripts\\python.exe sky.py --listen    always-on 'hey sky' wake word
  .venv/bin/python sky.py --hud           terminal dashboard (status/mic/chat)
  .venv/bin/python sky.py --say "..."     speak one line
  .venv/bin/python sky.py --see "q"       screenshot + understand
  .venv/bin/python sky.py --remind "t" --at 15:30   set reminder
  .venv/bin/python sky.py --briefing      run the morning briefing now
  .venv/bin/python sky.py --facts|--reset|--wipe
Daemon:  .venv/bin/python skyd.py   (reminders + daily briefing)
"""
import argparse
import json
import logging
import os
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core import agency, agent, eyes, llm, memory, mood, persona, telemetry, voice, wake  # noqa: E402
from core.redact import redact  # noqa: E402

log = logging.getLogger("sky")

DEFAULT_CFG = {
    "base_url": "http://localhost:20128/v1",
    "model": "glm-5.3-flash",
    "api_key_env": "OMNIROUTE_API_KEY",
    "max_tokens": 900,
    "temperature": 0.6,
    "history_messages": 8,
    "summarize_threshold": 40,
    "history_cap": 300,
    "input_char_cap": 4000,
    "speak_replies": True,
    "briefing_time": "09:00",
    "vision_model": "",
    "wake_phrase": "hey sky",
    "wake_threshold": 0.5,
    "wake_ack": True,
}


def setup_logging():
    (ROOT / "logs").mkdir(exist_ok=True)
    h = RotatingFileHandler(ROOT / "logs" / "sky.log", maxBytes=1_000_000, backupCount=3)
    h.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[h])


def load_cfg() -> dict:
    cfg = dict(DEFAULT_CFG)
    p = ROOT / "config.json"
    if p.exists():
        try:
            cfg.update(json.loads(p.read_text()))
        except (ValueError, OSError) as e:
            log.warning("config.json unreadable (%s) — defaults", e)
    return cfg


def build_system(cfg, mem) -> str:
    try:
        from core import learner
        learned = learner.digest(10)
    except Exception:
        learned = ""
    return persona.system_prompt(
        persona.load_persona(ROOT / "persona.md"), mem.facts_block(), mem.get_summary(),
        knowledge=persona.load_knowledge(ROOT / "knowledge.txt"), learned=learned)


def maybe_summarize(cfg, mem):
    try:
        ids, transcript = mem.oldest_beyond(keep=cfg.get("history_messages", 8))
        if not ids:
            return
        old = mem.get_summary()
        compressed = llm.chat(cfg, [
            {"role": "system", "content":
                "Compress this conversation transcript into at most 120 words. "
                "Keep facts about the user, decisions, and open topics. "
                "Output only the summary."},
            {"role": "user", "content": (f"Previous summary:\n{old or '(none)'}\n\n"
                                         f"New transcript:\n{transcript}")}
        ], max_tokens=1200, temperature=0.2)  # GLM thinking burns 300-700 tok; 350 left content empty → skip
        mem.set_summary(((old + "\n\n") if old else "") + compressed)
        mem.delete_messages(ids)
        log.info("summarized %d evicted messages", len(ids))
    except Exception as e:
        log.warning("summarization skipped: %s", e)


def speak_if_enabled(cfg, text):
    if cfg.get("speak_replies") and text and len(text) < 800:
        try:
            voice.speak(text)
        except voice.VoiceUnavailable as e:
            log.info("no audio path: %s", e)
        except Exception as e:
            log.warning("speak failed: %s", e)


def confirm_gate(cmd: str) -> bool:
    """REPL confirm gate for non-read-only shell commands."""
    print(f"\nsky wants to run: {cmd}")
    try:
        ans = input("   allow? [Enter=yes / n=no] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return ans in ("", "y", "yes", "ok")


def agent_turn(cfg, mem, text, confirm_fn) -> str:
    """One full agentic turn (tools + memory)."""
    text = redact(text)  # secrets never enter history, logs, or the model
    mood.update(text)  # emotional telemetry shifts with how sir treats SKY
    mem.extract_facts(text)  # before prompt: facts stated now count now
    history = [{"role": r, "content": c} for r, c in mem.history(cfg.get("history_messages", 8))]
    cfg["_agent_system"] = agent.AGENT_PROMPT + "\n\n" + build_system(cfg, mem)
    _agency = agency.override_block(ROOT)   # active Agency specialist persona
    if _agency:
        cfg["_agent_system"] += "\n\n" + _agency
    cfg["_agent_system"] += "\n\n" + mood.prompt_block()  # live mood -> tone
    reply = redact(agent.run_agent(cfg, mem, text, history, confirm_fn=confirm_fn))
    mem.append_message("user", text, cap=cfg.get("history_cap", 300))
    mem.append_message("assistant", reply, cap=cfg.get("history_cap", 300))

    # Summarization is a full LLM call (~5-10s on a reasoning model) — never make
    # the voice path wait for it. Run it in a background thread with its own
    # sqlite connection; failures are log-only by design.
    def _summarize_bg():
        time.sleep(1.0)  # let this turn's writes commit first
        try:
            m = memory.Memory(ROOT / "data" / "memory.db")
            maybe_summarize(cfg, m)
            m.close()
        except Exception as e:
            logging.getLogger("sky").warning("bg summarization skipped: %s", e)

    threading.Thread(target=_summarize_bg, daemon=True).start()
    return reply


# ---------------- voice mode (P2) ----------------

def talk_mode(cfg, mem):
    if not voice.mic_available():
        print("No microphone visible to WSL — staying in text mode (replies still spoken).")
    print("Voice mode: [Enter]=start/stop recording, type text to type it, /exit to quit.")
    while True:
        try:
            cmd = input("\n[Enter]=talk | type=text | /exit> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if cmd == "/exit":
            return
        if cmd:
            reply = agent_turn(cfg, mem, cmd[:cfg.get("input_char_cap", 4000)], confirm_gate)
            say_text, show_text = persona.split_bilingual(reply)
            print("sky>", show_text)
            speak_if_enabled(cfg, say_text)
            continue
        if not voice.mic_available():
            print("(mic unavailable — type instead)")
            continue
        stop = threading.Event()
        print("listening... (Enter to stop)")
        t = threading.Thread(target=lambda: (input(), stop.set()), daemon=True)
        t.start()
        try:
            audio, secs = voice.record_until(stop)
        except voice.VoiceUnavailable as e:
            print(f"(mic error: {e})")
            continue
        print(f"recorded {secs:.1f}s")
        said = voice.transcribe(audio)
        if not said:
            print("(nothing heard)")
            continue
        print("you said:", said)
        reply = agent_turn(cfg, mem, said[:cfg.get("input_char_cap", 4000)], confirm_gate)
        say_text, show_text = persona.split_bilingual(reply)
        print("sky>", show_text)
        speak_if_enabled(cfg, say_text)


# ---------------- wake-word mode (P6) ----------------

def listen_mode(cfg, mem):
    """Always-on: openWakeWord -> capture utterance -> voice chat."""
    import collections
    import numpy as np
    import sounddevice as sd

    if not voice.mic_available():
        print("No microphone visible — wake-word mode needs a mic.")
        return
    phrase = cfg.get("wake_phrase", "hey sky")
    thr = float(cfg.get("wake_threshold", 0.5))
    ack = bool(cfg.get("wake_ack", True))
    max_sec = float(os.environ.get("SKY_LISTEN_MAX_SEC", "0") or 0)
    wm = wake.model()
    # NOTE: the bundled openWakeWord model is pretrained on the jarvis
    # sound pattern; "hey sky" is close enough to trigger it, and the
    # transcript filter (core/wake.py) accepts either phrase.
    print(f"P6 listening for '{phrase}' (score>={thr}). Ctrl+C to stop.")

    pending = collections.deque()
    rec = []
    acc = np.empty((0,), dtype=np.float32)
    state = {"mode": "idle", "spoke": False, "last_voice": 0.0}
    floor = 0.002  # adaptive noise floor (RMS)

    def cb(indata, frames, t, status):
        pending.append(indata.copy())

    stop = threading.Event()
    if max_sec:
        threading.Thread(target=lambda: (time.sleep(max_sec), stop.set()),
                         daemon=True).start()

    def idle_frame(frame):
        nonlocal floor
        rms = float(np.sqrt(np.mean(frame * frame)))
        floor = 0.99 * floor + 0.01 * rms
        s = wake.score(wm, frame)
        if s >= thr:
            print(f"\n[woke: score={s:.2f}]")
            wake.reset(wm)
            pending.clear()
            if ack:
                state["mode"] = "busy"
                try:
                    voice.speak("Yes, sir?")
                except Exception:
                    pass
                pending.clear()
            state["mode"] = "rec"
            state["spoke"] = False
            state["last_voice"] = time.time()
            rec.clear()

    def handle_utterance():
        state["mode"] = "busy"
        audio = np.concatenate(rec) if rec else None
        rec.clear()
        said = ""
        try:
            said = voice.transcribe(audio)
        except Exception as e:
            log.warning("transcribe failed: %s", e)
        said = wake.strip_phrase(said)
        if not said:
            print("(nothing heard)")
            try:
                voice.speak("I did not catch that, sir.")
            except Exception:
                pass
        else:
            print("you said:", said)
            try:
                reply = agent_turn(cfg, mem, said[:cfg.get("input_char_cap", 4000)],
                                   confirm_gate)
                say_text, show_text = persona.split_bilingual(reply)
                print("sky>", show_text)
                speak_if_enabled(cfg, say_text)
            except llm.LLMDown as e:
                print(f"sky> (offline) brain unreachable — {e}")
                try:
                    voice.speak("Brain is unreachable, sir. Check OmniRoute.")
                except Exception:
                    pass
        pending.clear()
        wake.reset(wm)
        time.sleep(0.5)  # cooldown against speaker echo re-trigger
        state["mode"] = "idle"
        state["spoke"] = False

    def rec_frame(frame):
        rms = float(np.sqrt(np.mean(frame * frame)))
        thresh = max(0.004, 3.5 * floor)
        now = time.time()
        rec.append(frame)
        if rms > thresh:
            state["spoke"] = True
            state["last_voice"] = now
        dur = sum(len(f) for f in rec) / 16000
        if (state["spoke"] and now - state["last_voice"] > 1.2) or dur >= 12:
            handle_utterance()
        elif not state["spoke"] and dur >= 2.5:
            state["mode"] = "idle"  # echo/noise blip, not speech
            wake.reset(wm)

    try:
        with sd.InputStream(samplerate=16000, channels=1, dtype="float32",
                            blocksize=1280, callback=cb):
            while not stop.is_set():
                if not pending:
                    time.sleep(0.01)
                    continue
                acc = np.concatenate([acc, pending.popleft().flatten()])
                while len(acc) >= 1280:
                    frame, acc = acc[:1280], acc[1280:]
                    if state["mode"] == "idle":
                        idle_frame(frame)
                    elif state["mode"] == "rec":
                        rec_frame(frame)
                    # busy: drop mic frames while thinking/speaking
    except KeyboardInterrupt:
        print("\nlisten stopped")
    finally:
        print("listen ended")


HELP = """commands: /exit | /help | /reset (clear history, keep facts) | /facts | /wipe
          /talk (voice mode) | /listen (wake word) | /see [question] | /status | /remind text --at HH:MM"""


def repl(cfg, mem):
    print("SKY online — phase 1-5 build. Hands on, mic optional, daemon separate.")
    print(HELP)
    while True:
        try:
            text = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text in ("/exit", "/quit"):
            break
        if text == "/help":
            print(HELP)
            continue
        if text == "/reset":
            mem.reset_history()
            print("sky> Chat history cleared, sir. I kept what I know about you.")
            continue
        if text == "/facts":
            print(mem.facts_block())
            continue
        if text == "/wipe":
            mem.wipe()
            print("sky> Memory wiped. We start anew, sir.")
            continue
        if text == "/talk":
            talk_mode(cfg, mem)
            continue
        if text == "/listen":
            listen_mode(cfg, mem)
            continue
        if text == "/status":
            print(telemetry.report())
            continue
        if text.startswith("/see"):
            q = text[4:].strip()
            try:
                print(eyes.understand(q, cfg))
            except Exception as e:
                print(f"sky> eyes failed: {e}")
            continue
        if len(text) > cfg.get("input_char_cap", 4000):
            print("sky> That message is too long, sir.")
            continue
        try:
            reply = agent_turn(cfg, mem, text, confirm_gate)
            say_text, show_text = persona.split_bilingual(reply)
            print("sky>", show_text)
            speak_if_enabled(cfg, say_text)
        except llm.LLMDown as e:
            print(f"sky> (offline) Brain unreachable, sir — {e}\n"
                  f"     Check OmniRoute on :20128. Text mode continues.")
        except Exception as e:
            log.exception("turn failed")
            print(f"sky> (error) {e}")


def main():
    ap = argparse.ArgumentParser(description="SKY")
    ap.add_argument("--ask", help="one-shot agentic answer")
    ap.add_argument("--talk", action="store_true", help="voice mode")
    ap.add_argument("--listen", action="store_true",
                    help="always-on wake word ('hey sky') voice mode")
    ap.add_argument("--hud", action="store_true", help="Textual dashboard (sky_hud.py)")
    ap.add_argument("--say", help="speak one line and exit")
    ap.add_argument("--see", nargs="?", const="", help="screenshot + understand")
    ap.add_argument("--remind", help="reminder text (use with --at)")
    ap.add_argument("--at", help="HH:MM | in 10m | in 2h | YYYY-MM-DD HH:MM")
    ap.add_argument("--briefing", action="store_true", help="run briefing now")
    ap.add_argument("--facts", action="store_true")
    ap.add_argument("--reset", action="store_true", help="clear chat history")
    ap.add_argument("--wipe", action="store_true", help="nuke all memory")
    args = ap.parse_args()

    setup_logging()
    cfg = load_cfg()
    mem = memory.Memory(ROOT / "data" / "memory.db")

    if args.wipe:
        mem.wipe()
        print("memory wiped")
    elif args.facts:
        print(mem.facts_block())
        print("reminders:", mem.pending_reminders() or "none")
    elif args.reset:
        mem.reset_history()
        print("history cleared (facts kept)")
    elif args.say:
        print("speaking:", args.say)
        try:
            print("backend:", voice.speak(args.say))
        except Exception as e:
            print(f"audio unavailable: {e}")
    elif args.talk:
        talk_mode(cfg, mem)
    elif args.listen:
        listen_mode(cfg, mem)
    elif args.hud:
        import sky_hud
        sky_hud.run(cfg, mem)
    elif args.see is not None:
        print(eyes.understand(args.see or "What is on this screen?", cfg))
    elif args.remind is not None:
        from core.agent import _parse_time
        due = _parse_time(args.at or "in 1m")
        if due is None:
            print("cannot parse --at (use HH:MM, 'in 10m', 'in 2h', 'YYYY-MM-DD HH:MM')")
            sys.exit(2)
        mem.add_reminder(args.remind[:200], due)
        print(f"reminder set for {time.strftime('%Y-%m-%d %H:%M', time.localtime(due))} "
              f"(skyd must be running to fire it)")
    elif args.briefing:
        from skyd import briefing
        briefing(mem)
    elif args.ask:
        try:
            text = args.ask.strip()[:cfg.get("input_char_cap", 4000)]
            cfg["_agent_system"] = agent.AGENT_PROMPT + "\n\n" + build_system(cfg, mem)
            history = [{"role": r, "content": c} for r, c in mem.history(cfg.get("history_messages", 8))]
            print(agent.run_agent(cfg, mem, text, history, confirm_fn=None))
        except llm.LLMDown as e:
            print(f"(offline) brain unreachable: {e}")
    else:
        repl(cfg, mem)
    mem.close()


if __name__ == "__main__":
    main()
