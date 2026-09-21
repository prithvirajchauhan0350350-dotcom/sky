#!/usr/bin/env python3
"""SKY - her own desktop face. Native window, no browser.

A softly shaded girl who blinks, breathes, follows your cursor with her
eyes and lip-syncs every line she speaks. The chat box runs the full SKY
agent (tools + memory + facts). This app also serves the hologram control
API on 127.0.0.1:20129, so skyd reminders and voice.speak() hand their
lines to HER whenever she is on screen.

Run:   .venv/Scripts/python.exe sky_app.py
Demo:  .venv/Scripts/python.exe sky_app.py --demo
Quit:  .venv/Scripts/python.exe sky_app.py --quit
"""
import argparse
import logging
import math
import queue
import random
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import tkinter as tk
from tkinter import messagebox
from tkinter.scrolledtext import ScrolledText

from PIL import Image, ImageDraw, ImageFilter, ImageTk

import sky as sky_core
from core import voice
from spy_avatar import build_viseme_timeline, play_animated, synthesize_with_timing

PORT = 20129                  # same control API as hologram_app.py
TMP = ROOT / "data" / "face"
RATE = voice.TTS_RATE         # one knob: env SKY_TTS_RATE (default +5%)

W, STAGE_H = 560, 470         # stage = the girl's area
CHAT_H = 226                  # chat area height
GW, GH = 420, 460             # girl sprite size
GX, GY = 70, 4                # her position inside the stage
S = 2                         # supersampling for smooth edges

BG_TOP = (18, 16, 28)
BG_BOT = (8, 8, 14)
GLOW = (255, 120, 170)

SKIN = (255, 224, 206)
SKIN_HI = (255, 240, 228)
SKIN_DK = (240, 194, 178)
SKIN_DK2 = (214, 160, 148)
BLUSH = (255, 150, 150)
HAIR = (80, 53, 63)
HAIR_DK = (56, 36, 45)
HAIR_HI = (140, 103, 115)
LASH = (40, 26, 34)
LIPS = (226, 108, 122)
MOUTH_IN = (94, 34, 46)
TONGUE = (255, 134, 144)
IRIS = (150, 96, 58)
IRIS_LT = (200, 140, 88)
PUPIL = (34, 21, 13)
EYEW = (253, 251, 252)
BROW = (64, 42, 52)
DRESS = (216, 98, 124)
DRESS_DK = (172, 70, 96)
COLLAR = (250, 244, 248)
RIBBON = (255, 96, 128)


# ---------------- the girl ----------------

def _ear(d, cx, cy, r, flip=False):
    """Simple shaded ear at (cx, cy)."""
    for rr, col, off in ((r, SKIN_DK2, 0), (r - 1, SKIN, -1)):
        d.ellipse((cx - rr + off, cy - rr, cx + rr + off, cy + rr), fill=col)
    d.arc((cx - r + 3, cy - r // 2, cx + r - 3, cy + r // 2),
          start=250, end=110, fill=SKIN_DK2, width=2)


def _neck_and_shoulders(d, cx, sway, chin_y, bottom_y):
    """Neck, shoulders, dress + white sailor collar, clipped at the bottom."""
    ny = chin_y - 10
    d.polygon([(cx - 20 + sway, ny), (cx + 20 + sway, ny),
               (cx + 27 + sway, ny + 62), (cx - 27 + sway, ny + 62)],
              fill=SKIN)
    d.polygon([(cx - 24 + sway, ny), (cx - 10 + sway, ny),
               (cx - 16 + sway, ny + 60), (cx - 29 + sway, ny + 58)],
              fill=SKIN_DK)  # neck shading
    sy = ny + 38   # shoulder line
    # dress: one broad dome flowing off the bottom edge
    d.ellipse((cx - 195 + sway, sy, cx + 195 + sway, bottom_y + 170),
              fill=DRESS)
    d.ellipse((cx - 150 + sway, sy + 26, cx + 150 + sway, bottom_y + 260),
              fill=DRESS_DK)  # shading under the collar
    d.ellipse((cx - 150 + sway, sy + 40, cx + 150 + sway, bottom_y + 274),
              fill=DRESS)
    # white sailor collar
    d.pieslice((cx - 74 + sway, sy - 26, cx + 74 + sway, sy + 64),
               start=180, end=360, fill=COLLAR)
    d.line((cx - 62 + sway, sy + 19, cx + 62 + sway, sy + 19),
           fill=(226, 216, 224), width=3)
    d.polygon([(cx - 14 + sway, sy + 16), (cx + 14 + sway, sy + 16),
               (cx + sway, sy + 42)], fill=(226, 216, 224))  # collar notch
    # ribbon
    d.polygon([(cx - 20 + sway, sy + 22), (cx + sway, sy + 32),
               (cx - 20 + sway, sy + 42)], fill=RIBBON)
    d.polygon([(cx + 20 + sway, sy + 22), (cx + sway, sy + 32),
               (cx + 20 + sway, sy + 42)], fill=RIBBON)


def _eyeball(d, cx, cy, look, open_f, scale=1.0):
    """One big cute eye. look=(dx,dy) pupil offset; open_f 0..1 lid amount."""
    rx, ry_full = int(20 * scale), int(25 * scale)
    ry = max(2, int(ry_full * open_f))
    if ry <= 2:
        d.line((cx - rx, cy, cx + rx, cy), fill=LASH, width=4)
        return
    d.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=EYEW)
    px, py = cx + look[0], cy + look[1]
    ir = int(16 * scale)
    if py - ir > cy - ry and py + ir < cy + ry:
        d.ellipse((px - ir, py - ir, px + ir, py + ir), fill=IRIS)
        d.ellipse((px - ir + 3, py - ir + 3, px + ir - 3, py + ir - 3), fill=IRIS_LT)
        pr = max(3, int(6.5 * scale))
        d.ellipse((px - pr, py - pr, px + pr, py + pr), fill=PUPIL)
        # twin sparkles
        d.ellipse((px - pr - 3, py - pr - 6, px - pr + 4, py - pr + 1),
                  fill=(255, 255, 255, 235))
        d.ellipse((px + pr - 6, py + pr - 4, px + pr - 1, py + pr + 1),
                  fill=(255, 255, 255, 150))
    d.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), outline=LASH, width=3)
    lid = int((1.0 - open_f) * ry * 2)
    if lid > 0:
        d.polygon([(cx - rx - 2, cy - ry - 2), (cx + rx + 2, cy - ry - 2),
                   (cx + rx + 2, cy - ry + lid), (cx - rx - 2, cy - ry + lid)], fill=SKIN)
        d.line((cx - rx, cy - ry + lid, cx + rx, cy - ry + lid), fill=LASH, width=3)
    d.arc((cx - rx - 3, cy - ry - 5, cx + rx + 3, cy + ry + 5),
          start=195, end=345, fill=LASH, width=5)  # upper lashline


def _brow(d, cx, cy, angry=0.0):
    w = 30
    tilt = int(7 * angry)
    d.line((cx - w, cy + tilt, cx + w, cy - tilt), fill=BROW, width=5)


def _mouth(d, cx, cy, openness, width_f, scale=1.0):
    """Mouth. openness 0..1, width_f 0..1 (wide = 'ee', narrow = 'oo')."""
    w = max(6, int(17 * (0.55 + 0.45 * width_f) * scale))
    o = int(15 * openness * scale)
    if o <= 1:
        d.arc((cx - w, cy - 6, cx + w, cy + 8), start=20, end=160,
              fill=MOUTH_IN, width=3)
        return
    d.ellipse((cx - w, cy - o // 2, cx + w, cy + o // 2 + 2), fill=MOUTH_IN)
    if o >= 8:
        d.ellipse((cx - w + 2, cy - o // 2, cx + w - 2, cy + o // 2), fill=TONGUE)
    d.arc((cx - w, cy - o // 2 - 2, cx + w, cy + o // 2 + 4),
          start=15, end=165, fill=LIPS, width=3)


def render_girl(frame, w_px, h_px, look, mouth, mood, blink):
    """Draw one frame. Chibi-cute plan: big head (face bottom ~62% down),
    hair as a framing cap + side locks, small body at the bottom edge.
    frame: 0..1 breathing phase; mouth=(open,width);
    mood: idle/listening/thinking/speaking; blink: 0..1 (1=open)."""
    img = Image.new("RGB", (w_px * S, h_px * S), (14, 12, 22))
    d = ImageDraw.Draw(img, "RGBA")
    cx = w_px * S // 2
    bottom = h_px * S
    sway = math.sin(frame * math.tau) * 2.0 * S
    bob = math.sin(frame * math.tau * 2) * 1.0 * S
    cx = cx + sway
    top = 34 * S + bob          # top of the hair (leave room for the ahoge)

    # ---- head plan (all y offsets from `top`, x from cx) ----
    head_w = 105 * S            # half-width of the face oval
    forehead_y = top + 46 * S   # face oval top
    chin_y = top + 262 * S      # face bottom (head ~62% of the frame)
    face_cy = (forehead_y + chin_y) // 2

    # ---------- back hair: big soft mass behind everything ----------
    d.ellipse((cx - 128 * S, top, cx + 128 * S, chin_y + 60 * S), fill=HAIR_DK)
    # long back hair falling behind the shoulders
    d.rounded_rectangle((cx - 122 * S, chin_y - 40 * S, cx + 122 * S, bottom),
                        radius=70 * S, fill=HAIR_DK)

    # ---------- neck + body (BEHIND the face chin) ----------
    _neck_and_shoulders(d, cx, sway, chin_y, bottom)

    # ---------- face ----------
    fw = head_w
    d.ellipse((cx - fw, forehead_y, cx + fw, chin_y), fill=SKIN)
    # soft chin taper
    d.polygon([(cx - 66 * S, chin_y - 52 * S), (cx + 66 * S, chin_y - 52 * S),
               (cx, chin_y + 26 * S)], fill=SKIN)
    # soft forehead highlight (no hard rim — it read as a stripe on the cheek)
    d.ellipse((cx - 74 * S, forehead_y + 30 * S, cx + 26 * S, forehead_y + 66 * S),
              fill=SKIN_HI)

    # (ears stay hidden under the hair — visible blobs read as droopy skin)

    # ---------- front hair: cap + swept bangs + side locks ----------
    d.ellipse((cx - 122 * S, top - 12 * S, cx + 122 * S, face_cy + 10 * S), fill=HAIR)
    # bangs: overlapping pointed locks sweeping over the forehead
    bangs = (((-92, -6), (-58, 66), (-34, 40), (-52, -2)),
             ((-48, 62), (-16, -4), (10, 58), (-8, 70)),
             ((6, 56), (34, -6), (58, 60), (24, 68)),
             ((52, 58), (86, -2), (94, 52), (66, 66)))
    for pts in bangs:
        d.polygon([(cx + x * S, forehead_y + y * S) for x, y in pts], fill=HAIR)
    # side locks framing the face, tapering past the chin
    d.polygon([(cx - 112 * S, top + 46 * S), (cx - 86 * S, top + 68 * S),
               (cx - 96 * S, chin_y + 80 * S), (cx - 124 * S, chin_y + 56 * S)],
              fill=HAIR)
    d.polygon([(cx + 112 * S, top + 46 * S), (cx + 86 * S, top + 68 * S),
               (cx + 96 * S, chin_y + 80 * S), (cx + 124 * S, chin_y + 56 * S)],
              fill=HAIR)
    # ahoge (springy top strand)
    ah = math.sin(frame * math.tau * 1.7) * 7 * S
    d.line([(cx - 4 * S, top - 8 * S), (cx + 6 * S, top - 34 * S + ah),
            (cx + 22 * S, top - 20 * S + ah * 0.4)], fill=HAIR, width=int(5 * S))
    # hair shine band
    for i, a in enumerate((80, 130, 80)):
        d.arc((cx - 92 * S, top - 4 * S, cx + 92 * S, top + 74 * S),
              start=205 + i * 14, end=245 + i * 14,
              fill=HAIR_HI + (a,), width=int(3.2 * S))

    # ---------- features ----------
    ey_y = top + 148 * S
    look_s = (look[0] * S, look[1] * S)
    angry = 1.0 if mood == "thinking" else (0.35 if mood == "listening" else 0.0)
    _brow(d, cx - 50 * S, ey_y - 34 * S, angry)
    _brow(d, cx + 50 * S, ey_y - 34 * S, -angry if angry else 0)
    _eyeball(d, cx - 50 * S, ey_y, look_s, blink, scale=1.05)
    _eyeball(d, cx + 50 * S, ey_y, look_s, blink, scale=1.05)
    # nose: a tiny dot — the long arc read as a scar line
    d.ellipse((cx - 2 * S, chin_y - 56 * S, cx + 2 * S, chin_y - 52 * S),
              fill=SKIN_DK2)

    # ---------- blush on the cheeks ----------
    ba = 105 if mood != "listening" else 150
    for bx in (cx - 74 * S, cx + 74 * S):
        d.ellipse((bx - 22 * S, chin_y - 66 * S, bx + 22 * S, chin_y - 40 * S),
                  fill=BLUSH + (ba,))
        for i in range(3):
            x = bx - 10 * S + i * 10 * S
            d.line((x, chin_y - 62 * S, x - 6 * S, chin_y - 44 * S),
                   fill=BLUSH + (min(255, ba + 40),), width=2)

    _mouth(d, cx + 2 * S, chin_y - 26 * S, mouth[0], mouth[1])
    return img


def compose_stage(frame, look, mouth, mood, blink, w_px, h_px):
    """Girl + gradient backdrop + floor glow, supersampled then downscaled."""
    W2, H2 = w_px * S, h_px * S
    bg = Image.new("RGB", (W2, H2))
    dg = ImageDraw.Draw(bg)
    for y in range(H2):
        t = y / max(1, H2 - 1)
        dg.line([(0, y), (W2, y)], fill=tuple(int(a + (b - a) * t)
                 for a, b in zip(BG_TOP, BG_BOT)))
    # soft glow behind her
    glow = Image.new("RGB", (W2, H2), (0, 0, 0))
    dgl = ImageDraw.Draw(glow)
    gcy = H2 // 2
    dgl.ellipse((W2 // 2 - 150 * S, gcy - 170 * S, W2 // 2 + 150 * S, gcy + 190 * S),
                fill=GLOW)
    glow = glow.filter(ImageFilter.GaussianBlur(60 * S))
    bg = Image.blend(bg, Image.blend(bg, glow.point(lambda v: min(255, v * 2)), 0.35), 0.9)
    girl = render_girl(frame, w_px, h_px, look, mouth, mood, blink)
    # paste girl centered with a soft shadow
    mask = girl.convert("L").point(lambda v: 255 if v > 16 else 0)
    shadow = Image.new("RGB", (W2, H2), (0, 0, 0))
    mblur = mask.filter(ImageFilter.GaussianBlur(8 * S))
    bg.paste(shadow, (2 * S, 3 * S), mblur.point(lambda v: v // 3))
    bg.paste(girl, (0, 0), mask)
    return bg.resize((w_px, h_px), Image.LANCZOS)


# ---------------- the app ----------------

class SkyApp:
    def __init__(self, demo=False):
        self.demo = demo
        self.cfg = sky_core.load_cfg()
        self.mem = None
        self.speech_q = queue.Queue(maxsize=3)
        self.mouth = (0.0, 0.6)
        self.state = "idle"
        self.blink = 1.0
        self.look = (0.0, 0.0)
        self.t0 = time.time()
        self.next_blink = time.time() + random.uniform(2, 5)
        self.frame = 0.0
        self.mouse = (W // 2, STAGE_H // 3)
        self.agent_busy = False
        self.rec = None
        self.rec_stop = threading.Event()
        self._photo = None
        self.blink_start = 0.0

        self.root = tk.Tk()
        self.root.title("SKY")
        self.root.configure(bg="#0c0a12")
        self.root.geometry(f"{W}x{STAGE_H + CHAT_H}+{GX}+{GY}")
        self.root.minsize(W, STAGE_H + CHAT_H)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<Escape>", lambda e: self._on_close())
        self.root.bind("<Destroy>", self._on_destroy_log)

        self.canvas = tk.Canvas(self.root, width=W, height=STAGE_H,
                                bg="#0c0a12", highlightthickness=0)
        self.canvas.pack(fill=tk.X)
        self.canvas.bind("<Motion>", self._on_motion)

        bar = tk.Frame(self.root, bg="#0c0a12")
        bar.pack(fill=tk.X)
        self.mic_btn = tk.Label(bar, text="  🎤 hold to talk  ", bg="#241b30",
                                fg="#f2a0c0", font=("Segoe UI", 10, "bold"), padx=10, pady=4)
        self.mic_btn.pack(side=tk.LEFT, padx=(8, 4), pady=4)
        for ev in ("<ButtonPress-1>", "<ButtonRelease-1>"):
            self.mic_btn.bind(ev, self._mic)
        self.mic_state = tk.Label(bar, text="idle", bg="#0c0a12", fg="#7f7690",
                                  font=("Segoe UI", 9))
        self.mic_state.pack(side=tk.LEFT)

        chat = tk.Frame(self.root, bg="#0c0a12")
        chat.pack(fill=tk.BOTH, expand=True)
        self.log = ScrolledText(chat, bg="#14101d", fg="#e6dff0", font=("Segoe UI", 10),
                                bd=0, height=8, state=tk.DISABLED, wrap=tk.WORD,
                                insertbackground="#e6dff0")
        self.log.pack(fill=tk.BOTH, expand=True, padx=8, pady=(2, 4))
        for tag, col in (("you", "#8fd3ff"), ("sky", "#ff9ecb"), ("sys", "#8a8098")):
            self.log.tag_configure(tag, foreground=col, font=("Segoe UI", 10, "bold"))
        entry_row = tk.Frame(chat, bg="#0c0a12")
        entry_row.pack(fill=tk.X)
        self.entry = tk.Text(entry_row, bg="#1b1526", fg="#f2ecfa", font=("Segoe UI", 11),
                             bd=0, height=2, wrap=tk.WORD, insertbackground="#f2ecfa")
        self.entry.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 4), pady=(0, 6))
        self.entry.bind("<Return>", self._send)
        self.send_btn = tk.Button(entry_row, text="➤", command=self._send,
                                  bg="#3a2450", fg="#ffb7d5", bd=0, width=3,
                                  font=("Segoe UI", 11, "bold"))
        self.send_btn.pack(side=tk.RIGHT, padx=(0, 8), pady=(0, 6))

        self.stage = Image.new("RGB", (W, STAGE_H), (14, 12, 22))
        self.photo = ImageTk.PhotoImage(self.stage)
        self.img_id = self.canvas.create_image(0, 0, image=self.photo, anchor=tk.NW)

        self.root.after(200, self._greet)
        self.root.after(30, self._tick)
        threading.Thread(target=self._speech_worker, daemon=True).start()

    # ---------- chat ----------
    def _say(self, who, text):
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, f"{who}: ", (who,))
        self.log.insert(tk.END, text + "\n")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def _greet(self):
        if self.demo:
            self._say("sys", "demo mode — watch her idle, blink, think, speak.")
        self._say("sys", "type below and press Enter. sky has tools and memory.")
        self.root.after(600, lambda: self.say("Hello sir, sky reporting for duty."))

    # ---------- animation ----------
    def _on_motion(self, ev):
        self.mouse = (ev.x, ev.y)

    def _tick(self):
        now = time.time()
        self.frame = (now - self.t0) % 4.0 / 4.0
        # blink cycle
        if now >= self.next_blink and self.state != "speaking":
            if self.blink >= 1.0:
                self.blink_start = now
                self.next_blink = now + random.uniform(2.5, 6.0)
            dt = now - self.blink_start
            self.blink = max(0.0, 1.0 - abs(dt - 0.09) / 0.09 * 1.0) if dt < 0.18 else 1.0
        elif self.state != "speaking":
            self.blink = 1.0
        # eyes follow cursor
        cx = W / 2
        dx = (self.mouse[0] - cx) / max(1, W / 2)
        dy = (self.mouse[1] - STAGE_H * 0.45) / max(1, STAGE_H / 2)
        self.look = (max(-1, min(1, dx)) * 6, max(-1, min(1, dy)) * 4)
        try:
            img = compose_stage(self.frame, self.look, self.mouth, self.state, self.blink, W, STAGE_H)
            self.photo.paste(img)
            self.canvas.itemconfigure(self.img_id, image=self.photo)
        except tk.TclError:
            return
        self.root.after(40, self._tick)  # ~25 fps

    # ---------- mouth / speech ----------
    def set_mouth(self, openness, width):
        self.mouth = (openness, width)

    def _set_state(self, st):
        self.state = st
        try:
            self.root.after(0, lambda: self.mic_state.config(text=st))
        except RuntimeError:
            pass

    def say(self, text):
        """Queue a line: she speaks it with lip sync and shows it in the log."""
        if not text:
            return
        self._say("sky", text[:800])
        try:
            self.speech_q.put_nowait(text)
        except queue.Full:
            pass

    def _speech_worker(self):
        while True:
            text = self.speech_q.get()
            if text is None:
                return
            self._set_state("speaking")
            self._speak_once(text)
            self._set_state("idle")
            self.mouth = (0.0, 0.6)

    def _speak_once(self, text):
        """Synthesize + play with real word timings driving her mouth."""
        text = voice.clean_for_speech(text)  # TTS copy; screen text stays raw
        if not text:
            return
        TMP.mkdir(parents=True, exist_ok=True)
        mp3 = TMP / f"face_{int(time.time() * 1000)}.mp3"
        try:
            path, words = synthesize_with_timing(text, voice.pick_voice(text), str(mp3), rate=RATE)
            frames = build_viseme_timeline(words) if words else []
            if path and frames:
                play_animated(path, frames, self.set_mouth)
            elif path:
                play_animated(path, [], self.set_mouth)
            else:
                voice.speak(text)  # fallback: plain voice, no lip sync
        except Exception as e:
            logging.getLogger("sky.face").warning("speak failed: %s", e)
            try:
                voice.speak(text)
            except Exception:
                pass
        finally:
            try:
                mp3.unlink(missing_ok=True)
            except Exception:
                pass
            self.set_mouth(0.0, 0.6)

    # ---------- agent ----------
    def _confirm(self, *args):
        desc = " | ".join(str(a) for a in args if str(a).strip())
        try:
            self._say("sys", f"sky wants to: {desc}")
            ok = messagebox.askyesno("SKY - allow action?", desc, parent=self.root)
        except Exception:
            ok = False
        self._say("sys", "allowed" if ok else "denied")
        return ok

    def _send(self, ev=None):
        text = self.entry.get("1.0", tk.END).strip()
        if not text:
            return "break"
        self.entry.delete("1.0", tk.END)
        if self.agent_busy:
            self._say("sys", "one moment - still answering the last one.")
            return "break"
        if text.startswith("/quit"):
            self._on_close()
            return "break"
        self._say("you", text)
        self.agent_busy = True
        self._set_state("thinking")
        threading.Thread(target=self._agent_thread, args=(text,), daemon=True).start()
        return "break"

    def _agent_thread(self, text):
        try:
            if self.mem is None:
                from core import memory as core_memory
                self.mem = core_memory.Memory(ROOT / "data" / "memory.db")
            reply = sky_core.agent_turn(self.cfg, self.mem, text, self._confirm)
        except Exception as e:
            logging.getLogger("sky.face").exception("agent turn failed")
            reply = f"(error) {e}"
        self.root.after(0, lambda: self._finish_turn(reply))

    def _finish_turn(self, reply):
        self.agent_busy = False
        self._set_state("idle")
        self.say(reply)

    # ---------- mic ----------
    def _mic(self, ev):
        if ev.type.name == "ButtonPress":
            if not voice.mic_available():
                self._say("sys", "no microphone visible to Windows.")
                return
            self.mouth = (0.1, 0.6)
            self._set_state("listening")
            self.rec_stop.clear()
            threading.Thread(target=self._record, daemon=True).start()
        else:
            self.rec_stop.set()

    def _record(self):
        try:
            audio, secs = voice.record_until(self.rec_stop)
            said = voice.transcribe(audio) if audio is not None else ""
        except Exception as e:
            said = ""
            self.root.after(0, lambda: self._say("sys", f"mic error: {e}"))
        self.root.after(0, lambda: self._mic_done(said))

    def _mic_done(self, said):
        if not said:
            self._say("sys", "(nothing heard)")
        else:
            self._say("you", said)
            self._send_text(said)

    def _send_text(self, text):
        if self.agent_busy:
            self._say("sys", "one moment - still answering the last one.")
            return
        self.agent_busy = True
        self._set_state("thinking")
        threading.Thread(target=self._agent_thread, args=(text,), daemon=True).start()

    # ---------- window / http ----------
    def _on_close(self):
        logging.getLogger("sky.face").info("on_close called (window X / quit path)")
        try:
            self.speech_q.put_nowait(None)
        except queue.Full:
            pass
        try:
            if self.mem:
                self.mem.close()
        except Exception:
            pass
        self.root.destroy()

    def _on_destroy_log(self, ev):
        if str(ev.widget) == ".":
            logging.getLogger("sky.face").info("root destroyed (event)")

    def run(self):
        try:
            self.httpd = ThreadingHTTPServer(("127.0.0.1", PORT), self._handler())
        except OSError as e:
            self.httpd = None  # something else owns the port
            logging.getLogger("sky.face").warning("control port %d busy: %s", PORT, e)
            self._say("sys", "port 20129 busy - reminder pop-ups share is off.")
        else:
            threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self._say("sys", f"sky face ready - control port {'up' if self.httpd else 'busy'}")
        self.root.mainloop()

    def _handler(self):
        """Build and return the request-handler CLASS (socketserver
        instantiates it per request; passing an instance would break)."""
        APP = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, code, body=b"OK"):
                self.send_response(code)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/ping":
                    return self._reply(200)
                return self._reply(404, b"nope")

            def do_POST(self):
                if self.path == "/say":
                    n = int(self.headers.get("Content-Length", 0) or 0)
                    form = urllib.parse.parse_qs(self.rfile.read(n).decode("utf-8", "replace"))
                    text = (form.get("text") or [""])[0].strip()
                    if not text:
                        return self._reply(400, b"empty")
                    try:
                        APP.speech_q.put_nowait(text[:1000])
                    except queue.Full:
                        return self._reply(503, b"BUSY")
                    return self._reply(200)
                if self.path == "/hide":
                    APP.root.after(0, lambda: APP.root.withdraw())
                    return self._reply(200)
                if self.path == "/show":
                    APP.root.after(0, lambda: APP.root.deiconify())
                    return self._reply(200)
                if self.path == "/quit":
                    self._reply(200)
                    APP.root.after(0, APP._on_close)
                    return
                return self._reply(404, b"nope")
        return H


# ---------------- demo / main ----------------

def run_demo():
    """No brain needed: she cycles idle -> listening -> thinking -> speaking."""
    app = SkyApp(demo=True)
    phrases = ["Hey, I am Sky. I was waiting for you.",
               "You can talk to me anytime, day or night.",
               "Kya haal hai? I hope your day is going well."]
    seq = ["idle", "listening", "thinking", "speaking", "idle"]
    def _cycle(i=0):
        st = seq[i % len(seq)]
        if st == "speaking":
            app.say(phrases[(i // len(seq)) % len(phrases)])
        else:
            app._set_state(st)
        app.root.after(2200, lambda: _cycle(i + 1))
    app.root.after(4000, _cycle)
    app.run()


def main():
    import logging
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser(description="SKY face")
    ap.add_argument("--demo", action="store_true", help="animation demo, no brain")
    ap.add_argument("--render-test", action="store_true",
                    help="render one frame to data/face/frame.png and exit")
    ap.add_argument("--quit", action="store_true", help="close a running sky face")
    args = ap.parse_args()

    if args.render_test:
        img = compose_stage(0.13, (2.0, 1.0), (0.45, 0.8), "idle", 1.0, W, STAGE_H)
        TMP.mkdir(parents=True, exist_ok=True)
        out = TMP / "frame.png"
        img.save(out)
        print(f"rendered {out}")
        return
    if args.quit:
        data = urllib.parse.urlencode({}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/quit", data=data, method="POST")
        try:
            urllib.request.urlopen(req, timeout=3).close()
            print("quit sent")
        except Exception as e:
            print(f"no running sky face ({e})")
        return
    app = SkyApp(demo=args.demo)
    if args.demo:
        phrases = ["Hey, I am Sky. I was waiting for you.",
                   "You can talk to me anytime, day or night."]
        seq = ["idle", "listening", "thinking", "speaking", "idle"]
        def _cycle(i=0):
            st = seq[i % len(seq)]
            if st == "speaking":
                app.say(phrases[(i // len(seq)) % len(phrases)])
            else:
                app._set_state(st)
            app.root.after(2200, lambda: _cycle(i + 1))
        app.root.after(4000, _cycle)
    app.run()


if __name__ == "__main__":
    main()
