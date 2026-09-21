"""
spy_hologram.py — Spy's avatar: an ember-glow hologram girl.
------------------------------------------------------------
Modeled on the user's reference image (NightCafe "ai hologram generator"):
a female bust — face, neck, shoulders — rendered as glowing holographic
light: ember-orange body, blue-tinted face, magenta accents, flowing
swept-back hair, and a field of twinkling particle sparks linked by faint
network lines, dissolving into light at the base.

Animation (all pure Tkinter, ~26 FPS, no GPU):

  * breathing bust — gentle sway and bob, sparkles constantly twinkle
  * blinking blue eyes with occasional glances
  * real LIP SYNC — set_mouth(openness, width) is driven by spy_avatar
    from the ACTUAL spoken audio (viseme keyframes on the real timeline)
  * expressions — set_expression("happy" / "alert" / "neutral")

Reacts to what Spy is doing (set_state from any thread):

  idle      slow sway, soft twinkle
  listening expanding sonar ripples around her
  thinking  fast bright scanline sweep + sparks accelerate
  speaking  mouth moves with the voice, head bobs

The window is borderless, always-on-top, transparent on Windows
(transparentcolor key): only the glowing figure shows and catches the
mouse — drag her anywhere; clicks elsewhere fall through.

Toggle from Spy's chat window, or demo alone:  python spy_hologram.py
"""

import math
import random
import time
import tkinter as tk

# --- look & feel -----------------------------------------------------------

BG_KEY = "#010101"      # exact colour made invisible (Windows transparency)
WIDTH, HEIGHT = 300, 460
CX = WIDTH // 2
FRAME_MS = 38           # ~26 FPS

# palette lifted from the reference: ember body, blue face, magenta sparks
EMBER_DEEP = "#7a2d00"
EMBER = "#c85a00"
EMBER_BRIGHT = "#ff9b3d"
EMBER_HOT = "#ffd27a"
FACE_TINT = "#8fb9ff"
FACE_EDGE = "#57c8ff"
FACE_DEEP = "#3d6fbf"
IRIS = "#57c8ff"
LIP = "#e8a0d8"
MAGENTA = "#ff9de2"
SPARK_COLORS = (EMBER_HOT, EMBER_BRIGHT, MAGENTA, "#7ad7ff", "#ffffff")

DIM = "#5a2a10"         # beam / emitter / idle scanline

STATES = ("idle", "listening", "thinking", "speaking")

# head geometry (canvas coordinates)
HX, HY = CX, 122        # head centre
HRX, HRY = 50, 64       # head radii


# ---------------------------------------------------------------------------
# Precomputed geometry
# ---------------------------------------------------------------------------

def _hair_strands():
    """Swept-back flowing strands: roots along the crown, cascading down
    the right side past the shoulders (like the reference)."""
    strands = []
    n = 9
    for k in range(n):
        f = k / (n - 1)                       # 0 = left crown, 1 = right
        theta = math.radians(-165 + 145 * f)  # root angle on the scalp
        rx = HX + math.cos(theta) * HRX * 0.92
        ry = HY + math.sin(theta) * HRY * 0.94
        spread = 26 + k * 3.2
        pts = [
            (rx, ry),
            (HX + (rx - HX) * 0.55 + 20, HY - HRY * 0.92),
            (HX + 38 + spread * 0.5, HY - HRY * 0.45),
            (HX + 52 + spread * 0.8, HY + 18),
            (HX + 58 + spread, HY + 78),
            (HX + 52 + spread, HY + 138 + k * 5),
            (HX + 44 + spread, HY + 190 + k * 6),
        ]
        width = 3.4 - abs(f - 0.62) * 3.0
        color = MAGENTA if k in (2, 6) else (EMBER_HOT if k % 2 else EMBER_BRIGHT)
        strands.append({"base": pts, "width": max(1.4, width), "color": color,
                        "phase": k * 0.8})
    return strands


_HAIR = _hair_strands()

# face circuit traces (the reference's network lines): points across the
# cheek / forehead / shoulder, drawn faint cyan with bright nodes
_CIRCUITS = [
    [(HX - 34, HY - 6), (HX - 18, HY + 8), (HX - 4, HY + 4), (HX + 10, HY + 20)],
    [(HX - 26, HY - 26), (HX - 6, HY - 30), (HX + 14, HY - 22), (HX + 30, HY - 28)],
    [(HX - 40, HY + 150), (HX - 18, HY + 172), (HX + 6, HY + 166), (HX + 26, HY + 188)],
    [(HX + 30, HY + 60), (HX + 44, HY + 84), (HX + 38, HY + 112)],
]
_NODES = [p for line in _CIRCUITS for p in (line[0], line[-1])]

# dissolve bands: the torso breaks into scattered light at the bottom
def _dissolve_bands():
    rnd = random.Random(7)
    bands = []
    for row in range(9):
        y = HY + 208 + row * 14
        n = max(2, 9 - row)
        segs = []
        for i in range(n):
            x = CX - 70 + rnd.uniform(0, 140)
            ln = rnd.uniform(8, 30) * (1.0 - row / 11)
            segs.append((x, y, x + ln, y + rnd.uniform(-2, 2)))
        bands.append(segs)
    return bands

_DISSOLVE = _dissolve_bands()


# ---------------------------------------------------------------------------


class Hologram:
    """The floating avatar. Lives on the GUI's Tk root; every public method
    is thread-safe (work is marshalled onto the Tk thread)."""

    def __init__(self, master: tk.Misc,
                 margin_right: int = 48, margin_bottom: int = 130):
        self.root = master
        self.state = "idle"
        self.expression = "neutral"
        self._visible = True
        self._running = False

        # mouth channel (visemes): current + target, lerped every frame
        self._mouth = (0.05, 0.5)
        self._mouth_target = (0.05, 0.5)
        # expression targets (smile lift, brow raise, eye open)
        self._expr = (0.0, 0.0, 1.0)

        # blink / glance scheduling
        self._next_blink = time.monotonic() + random.uniform(2, 5)
        self._blink_until = 0.0
        self._next_glance = time.monotonic() + random.uniform(2, 6)
        self._glance = 0.0
        self._glance_target = 0.0

        self.win = tk.Toplevel(master)
        self.win.overrideredirect(True)
        for attr, value in (("-topmost", True), ("-transparentcolor", BG_KEY)):
            try:
                self.win.attributes(attr, value)
            except tk.TclError:
                pass  # non-Windows: degrade to a solid dark panel
        self.win.configure(bg=BG_KEY)

        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        self.win.geometry(
            f"{WIDTH}x{HEIGHT}"
            f"+{sw - WIDTH - margin_right}+{sh - HEIGHT - margin_bottom}"
        )

        self.canvas = tk.Canvas(self.win, width=WIDTH, height=HEIGHT,
                                bg=BG_KEY, highlightthickness=0, bd=0)
        self.canvas.pack()

        self._create_items()
        self._bind_drag()
        self._start()

    # -- public API ----------------------------------------------------------

    def set_state(self, state: str):
        """Thread-safe: idle / listening / thinking / speaking."""
        if state in STATES:
            try:
                self.root.after(0, self._apply_state, state)
            except tk.TclError:
                pass

    def set_mouth(self, openness: float, width: float):
        """Thread-safe viseme channel (0..1, 0..1) from spy_avatar."""
        try:
            self.root.after(0, self._apply_mouth,
                            max(0.0, min(1.0, openness)),
                            max(0.0, min(1.0, width)))
        except tk.TclError:
            pass

    def set_expression(self, name: str):
        """Thread-safe: happy / alert / neutral."""
        table = {"happy": (1.0, 0.35, 0.92),
                 "alert": (0.0, 1.0, 1.18),
                 "neutral": (0.0, 0.0, 1.0)}
        if name in table:
            try:
                self.root.after(0, self._apply_expression, table[name])
            except tk.TclError:
                pass

    def show(self):
        try:
            self.win.deiconify()
            self.win.lift()
        except tk.TclError:
            return
        self._visible = True
        self._start()

    def hide(self):
        self._visible = False
        try:
            self.win.withdraw()
        except tk.TclError:
            pass

    def toggle(self) -> bool:
        """Show/hide; returns the new visibility."""
        if self._visible:
            self.hide()
        else:
            self.show()
        return self._visible

    # -- thread-entry appliers -------------------------------------------------

    def _apply_state(self, state):
        self.state = state

    def _apply_mouth(self, op, wd):
        self._mouth_target = (op, wd)

    def _apply_expression(self, params):
        self._expr = params

    def _start(self):
        if not self._running:
            self._running = True
            self.root.after(FRAME_MS, self._tick)

    def _tick(self):
        if not self._visible:
            self._running = False
            return
        try:
            self._render(time.monotonic())
        except tk.TclError:
            self._running = False
            return  # window destroyed — stop the loop
        self.root.after(FRAME_MS, self._tick)

    # -- item creation ---------------------------------------------------------

    def _create_items(self):
        c = self.canvas

        # projector: emitter disc + beam lines up into the body
        c.create_oval(CX - 78, HEIGHT - 12, CX + 78, HEIGHT - 2,
                      outline=DIM, width=1)
        self._beam = [c.create_line(CX, HEIGHT - 6,
                                    CX - 60 + i * 20, HY + 230,
                                    fill=DIM, width=1)
                      for i in range(7)]

        # --- bust: layered translucent shapes (dark → bright) ---
        # shoulders / torso
        self._torso = []
        for pts, fill, stip in (
            ((60, 336, 74, 258, 108, 228, 138, 214, 166, 214, 196, 230,
              228, 260, 242, 336), EMBER_DEEP, "gray50"),
            ((86, 322, 98, 266, 126, 240, 152, 232, 178, 240, 204, 268,
              216, 322), EMBER, "gray25"),
        ):
            self._torso.append(c.create_polygon(
                pts, fill=fill, outline=EMBER, width=1,
                smooth=True, stipple=stip))
        # neck
        c.create_polygon((HX - 16, HY + 52, HX + 16, HY + 52,
                          HX + 22, HY + 96, HX - 22, HY + 96),
                         fill="#c86a4a", outline=EMBER_BRIGHT, width=1,
                         stipple="gray50")
        # chest glow
        c.create_oval(CX - 62, HY + 118, CX + 62, HY + 212,
                      fill=EMBER, outline="", stipple="gray12")

        # --- dissolve bands (torso breaking into light) ---
        self._dissolve_items = [
            [c.create_line(*seg, fill=EMBER_BRIGHT if ri % 2 else EMBER_HOT,
                           width=2 if ri < 4 else 1)
             for seg in segs]
            for ri, segs in enumerate(_DISSOLVE)
        ]

        # --- hair (behind face features, over the scalp) ---
        self._hair_items = [
            c.create_line(0, 0, 0, 0, fill=s["color"], width=s["width"],
                          smooth=True, capstyle="round")
            for s in _HAIR
        ]

        # --- head ---
        c.create_oval(HX - HRX, HY - HRY, HX + HRX, HY + HRY,
                      fill=FACE_TINT, outline=FACE_EDGE, width=2,
                      stipple="gray50")
        c.create_oval(HX - HRX + 12, HY - HRY + 16,
                      HX + HRX - 12, HY + HRY - 8,
                      fill=FACE_DEEP, outline="", stipple="gray12")

        # face circuits + nodes
        for line in _CIRCUITS:
            c.create_line(*[v for p in line for v in p], fill=FACE_EDGE,
                          width=1, smooth=True)
        for (nx, ny) in _NODES:
            c.create_oval(nx - 1.5, ny - 1.5, nx + 1.5, ny + 1.5,
                          fill="#ffffff", outline="")

        # --- face features (moved every frame: sway + blink + mouth) ---
        # eyes: almond (core), iris, glint
        self._eyes = []
        for ex in (HX - 20, HX + 20):
            almond = c.create_oval(0, 0, 0, 0, fill="#dcecff",
                                   outline=FACE_DEEP, width=1)
            iris = c.create_oval(0, 0, 0, 0, fill=IRIS, outline="")
            glint = c.create_oval(0, 0, 0, 0, fill="#ffffff", outline="")
            self._eyes.append({"cx": ex, "cy": HY - 8, "almond": almond,
                               "iris": iris, "glint": glint})
        # brows
        self._brows = [c.create_line(0, 0, 0, 0, 0, 0, fill="#3a2a1a",
                                     width=2, smooth=True, capstyle="round")
                       for _ in range(2)]
        # nose
        self._nose = c.create_line(0, 0, 0, 0, fill=FACE_DEEP, width=1,
                                   smooth=True)
        # lips: upper arc + lower arc + interior
        self._mouth_in = c.create_oval(0, 0, 0, 0, fill="#1a1430", outline="")
        self._lip_up = c.create_line(0, 0, 0, 0, 0, 0, fill=LIP, width=2,
                                     smooth=True, capstyle="round")
        self._lip_lo = c.create_line(0, 0, 0, 0, 0, 0, fill=LIP, width=2,
                                     smooth=True, capstyle="round")
        # blush
        for bx in (HX - 32, HX + 32):
            c.create_oval(bx - 6, HY + 12, bx + 6, HY + 18,
                          fill=MAGENTA, outline="", stipple="gray75")

        # --- particles + network links ---
        rnd = random.Random(11)
        self._particles = []
        for i in range(42):
            self._particles.append({
                "fx": rnd.uniform(0, 1), "fy": rnd.uniform(0, 1),
                "speed": rnd.uniform(0.4, 1.4), "phase": rnd.uniform(0, 6.28),
                "size": rnd.uniform(1.2, 3.0),
                "color": rnd.choice(SPARK_COLORS),
            })
        self._spark_items = [
            c.create_oval(-2, -2, -1, -1, fill=p["color"], outline="")
            for p in self._particles
        ]
        self._link_items = [c.create_line(0, 0, 0, 0, fill=EMBER,
                                          width=1) for _ in range(12)]

        # --- listening ripples ---
        self._ripples = [c.create_oval(-1, -1, -1, -1, outline=MAGENTA,
                                       width=2, state="hidden")
                         for _ in range(3)]

        # --- scanline ---
        self._scan = c.create_line(0, 0, 0, 0, fill=EMBER_HOT, width=1)

    # -- per-frame render ------------------------------------------------------

    def _render(self, t: float):
        st = self.state
        c = self.canvas

        # global motion: sway + bob (speaking bobs faster)
        sway = 5.5 * math.sin(t * 0.55)
        bob = (2.6 * math.sin(t * 5.0)) if st == "speaking" \
            else (1.8 * math.sin(t * 0.9))
        spark_speed = 3.0 if st == "thinking" else 1.0

        # lerp mouth toward target (visemes arrive from the audio thread)
        k = 0.45
        self._mouth = tuple(cur + (tgt - cur) * k
                            for cur, tgt in zip(self._mouth, self._mouth_target))
        smile, brow_up, eye_open = self._expr

        # --- hair: wave the strands ---
        for item, s in zip(self._hair_items, _HAIR):
            pts = []
            for i, (x, y) in enumerate(s["base"]):
                w = math.sin(t * 1.25 + s["phase"] + i * 0.6) * (1.2 + i * 0.9)
                pts.append((x + sway * 0.35 + w, y + bob * 0.4))
            c.coords(item, *[v for p in pts for v in p])

        # --- eyes: blink + glance + expression ---
        now = t  # monotonic seconds
        if now > self._next_blink:
            self._blink_until = now + 0.13
            self._next_blink = now + random.uniform(2.6, 5.5)
        if now > self._next_glance:
            self._glance_target = random.uniform(-3.5, 3.5)
            self._next_glance = now + random.uniform(1.8, 5.0)
        self._glance += (self._glance_target - self._glance) * 0.12
        blinking = now < self._blink_until
        eye_h = (5.2 * eye_open) * (0.12 if blinking else 1.0)

        for eye in self._eyes:
            ex = eye["cx"] + sway
            ey = eye["cy"] + bob
            c.coords(eye["almond"], ex - 9, ey - eye_h, ex + 9, ey + eye_h)
            ir = 0.5 if blinking else 3.1
            ix = ex + self._glance
            c.coords(eye["iris"], ix - ir, ey - ir, ix + ir, ey + ir)
            g = 0.4 if blinking else 1.1
            c.coords(eye["glint"], ix - 1 + g * 0.2, ey - 1.4,
                     ix - 1 + g * 0.2 + g, ey - 1.4 + g)
            c.itemconfigure(eye["iris"], state="hidden" if blinking else "normal")
            c.itemconfigure(eye["glint"], state="hidden" if blinking else "normal")

        # --- brows (raise with expression) ---
        for item, bx in zip(self._brows, (HX - 20, HX + 20)):
            by = HY - 24 + bob - brow_up * 4
            c.coords(item, bx - 9 + sway, by + 1.5,
                     bx + sway, by - 1.2 - smile * 1.2,
                     bx + 9 + sway, by + 0.6)

        # --- nose ---
        c.coords(self._nose, HX - 2 + sway * 0.92, HY + 6 + bob,
                 HX + 1 + sway * 0.92, HY + 13 + bob)

        # --- mouth (viseme-driven) ---
        op, wd = self._mouth
        half = 11 + wd * 9
        sep = op * 12
        my = HY + 30 + bob
        mx = HX + sway * 0.88
        c.coords(self._mouth_in,
                 mx - half + 2, my - sep * 0.5, mx + half - 2, my + sep * 0.6)
        c.coords(self._lip_up,
                 mx - half, my, mx - half * 0.3, my - 2.4 - smile * 1.6,
                 mx + half * 0.3, my - 2.4 - smile * 1.6, mx + half, my)
        c.coords(self._lip_lo,
                 mx - half, my + sep * 0.55,
                 mx - half * 0.3, my + 3.2 + sep + smile * 1.4,
                 mx + half * 0.3, my + 3.2 + sep + smile * 1.4,
                 mx + half, my + sep * 0.55)
        c.itemconfigure(self._mouth_in,
                        state="normal" if sep > 1.2 else "hidden")

        # --- particles: drift up + twinkle ---
        links_used = 0
        pts_xy = []
        for i, (p, item) in enumerate(zip(self._particles, self._spark_items)):
            fy = (p["fy"] - t * 0.018 * p["speed"] * spark_speed) % 1.0
            x = 66 + p["fx"] * 168 + sway * 0.3
            y = 66 + fy * 356
            vis = 0.5 + 0.5 * math.sin(t * 2.6 * p["speed"] + p["phase"])
            if vis < 0.14:
                c.coords(item, -3, -3, -2, -2)
                pts_xy.append(None)
                continue
            r = p["size"] * (0.5 + vis)
            c.coords(item, x - r, y - r, x + r, y + r)
            pts_xy.append((x, y))
        # faint network lines between nearby live sparks
        for i in range(0, len(pts_xy) - 1, 3):
            a, b = pts_xy[i], pts_xy[i + 1]
            if links_used >= len(self._link_items):
                break
            item = self._link_items[links_used]
            links_used += 1
            if a and b:
                dx, dy = a[0] - b[0], a[1] - b[1]
                if dx * dx + dy * dy < 1600:
                    c.coords(item, a[0], a[1], b[0], b[1])
                    continue
            c.coords(item, -2, -2, -1, -1)

        # --- listening ripples around her ---
        if st == "listening":
            for i, item in enumerate(self._ripples):
                frac = (t * 0.6 + i / 3) % 1
                rx = HRX * (0.8 + frac * 1.6)
                ry = rx * 1.18
                c.coords(item, HX - rx, HY + 10 - ry,
                         HX + rx, HY + 10 + ry)
                c.itemconfigure(item, state="normal")
        else:
            for item in self._ripples:
                c.itemconfigure(item, state="hidden")

        # --- scanline (fast + bright while thinking) ---
        frac = (t * (2.8 if st == "thinking" else 0.4)) % 1
        y = 60 + frac * (HEIGHT - 90)
        c.coords(self._scan, 40, y, WIDTH - 40, y)
        c.itemconfigure(self._scan,
                        fill=EMBER_HOT if st == "thinking" else DIM,
                        width=2 if st == "thinking" else 1)

        # --- beam flicker ---
        if (t % 0.9) < 0.12:
            for item in self._beam:
                c.itemconfigure(item, fill=EMBER)

    # -- dragging (grab any glowing pixel and move the hologram) ---------------

    def _bind_drag(self):
        self.canvas.bind("<Button-1>", self._drag_start)
        self.canvas.bind("<B1-Motion>", self._drag_move)

    def _drag_start(self, event):
        self._drag_off = (event.x_root - self.win.winfo_x(),
                          event.y_root - self.win.winfo_y())

    def _drag_move(self, event):
        try:
            self.win.geometry(f"+{event.x_root - self._drag_off[0]}"
                              f"+{event.y_root - self._drag_off[1]}")
        except AttributeError:
            pass


if __name__ == "__main__":
    # Demo: cycles all states; the "speaking" phase drives the mouth with a
    # viseme-like pattern so you can see the lip sync channel working.
    _root = tk.Tk()
    _root.withdraw()
    _holo = Hologram(_root)
    _holo.set_expression("happy")

    def _fake_mouth_loop():
        if _holo.state == "speaking":
            tm = time.monotonic()
            op = max(0.0, math.sin(tm * 11.0)) * 0.9
            wd = 0.5 + 0.4 * math.sin(tm * 7.3)
            _holo.set_mouth(op, wd)
        else:
            _holo.set_mouth(0.05, 0.5)
        _root.after(40, _fake_mouth_loop)

    for _state, _ms in (("idle", 0), ("listening", 2500), ("thinking", 5000),
                        ("speaking", 7500), ("idle", 11500)):
        _root.after(_ms, lambda s=_state: _holo.set_state(s))
    _root.after(40, _fake_mouth_loop)
    _root.after(15000, _root.destroy)
    _root.mainloop()
