/* SKYFACE — human emotion engine for the Live2D girl.
   Drives facial parameters directly (shizuku has no expression files).
   Public API:
     SkyFace.init(model)            — wire to a loaded Live2D model
     SkyFace.react(text, holdMs)    — read a reply, feel + show the emotion
     SkyFace.hear(text)             — quick anticipatory face for user input
     SkyFace.set(name, holdMs)      — force an emotion ('happy','sad','angry',
                                      'surprised','shy','love','thinking','neutral')
     SkyFace.poke()                 — someone touched her
     SkyFace.speaking(bool)         — talking state (adds organic mouth-form life)
*/
"use strict";
window.SkyFace = (function () {
  let model = null;
  let activeName = "neutral";
  let activeUntil = 0;
  let tween = null;          // {from:{}, to:{}, t0, dur}
  let cur = {};              // current param values (union of used ids)
  let pending = null;        // emotion queued before init
  let speaking = false;
  let nextMicro = 0;         // timestamp of next idle micro-expression
  let micro = null;          // {to:{}, until}
  let raf = 0;

  const BASE_SMILE = 0.32;   // resting gentle smile

  /* parameter presets — shizuku param ids (verified against cdi3.json) */
  const PRESETS = {
    happy:     { PARAM_EYE_L_OPEN: .72, PARAM_EYE_R_OPEN: .72,
                 PARAM_EYE_BALL_KIRAKIRA: .7, PARAM_BROW_L_Y: .3,
                 PARAM_BROW_R_Y: .3, PARAM_MOUTH_FORM: 1, PARAM_TERE: .5,
                 PARAM_ANGLE_Z: 6, PARAM_BODY_Z: 2 },
    love:      { PARAM_EYE_L_OPEN: .5, PARAM_EYE_R_OPEN: .5,
                 PARAM_EYE_BALL_KIRAKIRA: 1, PARAM_MOUTH_FORM: .85,
                 PARAM_TERE: 1, PARAM_ANGLE_Z: -7, PARAM_BROW_L_Y: .25,
                 PARAM_BROW_R_Y: .25, PARAM_BODY_Z: -2 },
    sad:       { PARAM_EYE_L_OPEN: .5, PARAM_EYE_R_OPEN: .5,
                 PARAM_BROW_L_Y: .5, PARAM_BROW_R_Y: .5,
                 PARAM_BROW_L_FORM: -.8, PARAM_BROW_R_FORM: -.8,
                 PARAM_MOUTH_FORM: -.85, PARAM_MOUTH_OPEN_Y: .12,
                 PARAM_DONYORI: 1, PARAM_ANGLE_Y: -8, PARAM_ANGLE_Z: -5,
                 PARAM_BODY_Z: -3 },
    angry:     { PARAM_EYE_L_OPEN: .8, PARAM_EYE_R_OPEN: .8,
                 PARAM_BROW_L_Y: -.7, PARAM_BROW_R_Y: -.7,
                 PARAM_BROW_L_FORM: -1, PARAM_BROW_R_FORM: -1,
                 PARAM_MOUTH_FORM: -1, PARAM_ANGLE_Y: 6, PARAM_TERE: .3 },
    surprised: { PARAM_EYE_L_OPEN: 1.05, PARAM_EYE_R_OPEN: 1.05,
                 PARAM_BROW_L_Y: 1, PARAM_BROW_R_Y: 1,
                 PARAM_MOUTH_OPEN_Y: .55, PARAM_MOUTH_FORM: -.2,
                 PARAM_ANGLE_Z: -3, PARAM_EYE_BALL_Y: -.2 },
    shy:       { PARAM_EYE_L_OPEN: .55, PARAM_EYE_R_OPEN: .55,
                 PARAM_EYE_BALL_X: .55, PARAM_MOUTH_FORM: .55,
                 PARAM_TERE: 1, PARAM_ANGLE_Y: 10, PARAM_BROW_L_Y: .2,
                 PARAM_BROW_R_Y: .2, PARAM_BODY_Z: 2 },
    thinking:  { PARAM_EYE_BALL_X: -.65, PARAM_EYE_BALL_Y: .45,
                 PARAM_MOUTH_FORM: -.25, PARAM_BROW_R_Y: .45,
                 PARAM_BROW_L_Y: -.1, PARAM_ANGLE_Z: -4,
                 PARAM_EYE_L_OPEN: .8, PARAM_EYE_R_OPEN: .75 },
    neutral:   {}
  };
  const MOTION_FOR = { happy: "Tap", love: "Tap", shy: "Tap",
                       surprised: "FlickUp" };

  /* ---------- emotion detection (Hinglish + English) ---------- */
  const LEX = [
    ["love",      /love|pyaar|pyar|jaan|❤|😍|miss you|missed you|cute ho|beautiful|gorgeous|meri sky/g],
    ["happy",     /khush|great|awesome|nice|good job|yay|thanks|thank you|shukriya|dhanyavad|perfect|badhiya|zabardast|mast|accha|acha|wah|haha|lol|lolz|😄|😊|🙂|👍|congrats|shabash|well done|!{2,}/g],
    ["sad",       /sad|dukhi|dukh|sorry|maaf karo|maaf|fail|napas|rona|cry|😢|😔|😞|upset|thak gaya|thak gayi|akela|bura din|breakup/g],
    ["angry",     /angry|gussa|gussa hai|bakwas|useless|worst|hate|nafrat|chup|pagal|😠|😡|🤬|stupid|befkoof|bewakoof|bekaar|bekar/g],
    ["surprised", /kya \?!|what \?!|really\?|sach me|sachme|sachi|seriously\?|omg|whoa|wow|😮|😲|whaa|\?!/g],
    ["shy",       /sharma|sharminda|blush|😳|achha ji|acha ji|naughty|nautanki|hayee|haye/g],
    ["thinking",  /\?|kaise|kyun|kyu|why|how|kya|soch|confus|samajh|pata/g]
  ];
  function detect(text) {
    const t = " " + String(text || "").toLowerCase() + " ";
    const score = {};
    for (const [emo, re] of LEX) {
      const m = t.match(re);
      if (m) score[emo] = (score[emo] || 0) + m.length;
    }
    let best = "neutral", n = 0;
    for (const k in score) if (score[k] > n) { n = score[k]; best = k; }
    if (best === "neutral" && /\?\s*$/.test(t.trim())) best = "thinking";
    return best;
  }

  /* ---------- motion / idle management ---------- */
  function suspendIdle() {
    try {
      const mm = model.internalModel.motionManager;
      if (mm) { mm.stopAllMotions && mm.stopAllMotions(); mm.groups.idle = "__skyface__"; }
    } catch (e) {}
  }
  function resumeIdle() {
    try {
      const mm = model.internalModel.motionManager;
      if (mm) mm.groups.idle = "Idle";   // manager auto-restarts Idle
    } catch (e) {}
  }
  function playMotion(name) {
    if (!name) return;
    try { model.motion(name, undefined, 3); } catch (e) {}
  }

  /* ---------- tween helpers ---------- */
  function collect(ids) { for (const id of ids) if (!(id in cur)) cur[id] = id === "PARAM_MOUTH_FORM" ? BASE_SMILE : 0; }
  function ease(t) { return 1 - Math.pow(1 - t, 3); }
  function setEmotion(name, holdMs) {
    const preset = PRESETS[name] ? name : "neutral";
    collect(Object.keys(PRESETS[preset]));
    const from = {}; for (const k in cur) from[k] = cur[k];
    tween = { from, to: PRESETS[preset], t0: performance.now(), dur: preset === "neutral" ? 550 : 380 };
    activeName = preset;
    activeUntil = preset === "neutral" ? 0 : performance.now() + (holdMs || 4500);
    if (preset !== "neutral") { suspendIdle(); playMotion(MOTION_FOR[preset]); }
    else resumeIdle();
  }

  /* ---------- idle micro-life (glances, brow flicks) ---------- */
  function microEvent(now) {
    const roll = Math.random();
    if (roll < .45)
      micro = { to: { PARAM_EYE_BALL_X: (Math.random() * 1.1 - .55).toFixed(2) * 1,
                      PARAM_EYE_BALL_Y: (Math.random() * .5 - .15).toFixed(2) * 1 }, until: now + 1200 };
    else if (roll < .7)
      micro = { to: { PARAM_BROW_R_Y: .4, PARAM_EYE_BALL_X: -.3 }, until: now + 1400 };
    else if (roll < .9)
      micro = { to: { PARAM_MOUTH_FORM: .55, PARAM_TERE: .35 }, until: now + 1800 };
    else
      micro = { to: { PARAM_ANGLE_Z: 4, PARAM_BODY_Z: 1.5 }, until: now + 1500 };
    nextMicro = now + 7000 + Math.random() * 9000;
  }

  /* ---------- main loop ---------- */
  function tick(now) {
    raf = requestAnimationFrame(tick);
    if (!model) return;
    let cm;
    try { cm = model.internalModel.coreModel; } catch (e) { return; }
    if (!cm) return;

    /* emotion expiry → back to neutral */
    if (activeName !== "neutral" && now > activeUntil) setEmotion("neutral");

    /* tween progress */
    if (tween) {
      const t = Math.min(1, (now - tween.t0) / tween.dur), e = ease(t);
      for (const k in tween.to)
        cur[k] = (tween.from[k] !== undefined ? tween.from[k] : 0) +
                 (tween.to[k] - (tween.from[k] !== undefined ? tween.from[k] : 0)) * e;
      if (t >= 1) tween = null;
    }
    /* micro-expression tween (only while neutral-ish) */
    if (micro) {
      if (now > micro.until) micro = null;
      else if (activeName === "neutral") {
        const t = Math.min(1, (micro.until - now) / 1200);
        for (const k in micro.to) {
          if (!(k in cur)) cur[k] = 0;
          const peak = micro.to[k], dir = t > .5 ? (1 - t) * 2 : t * 2; // in-out
          cur[k] = cur[k] * .6 + peak * dir * .4;
        }
      }
    } else if (activeName === "neutral" && now > nextMicro) microEvent(now);

    /* write params with organic wobble */
    const wob = (id, i) => {
      const a = cur[id] !== undefined ? cur[id] : 0;
      const n = Math.sin(now / (620 + i * 137) + i * 2.1) * .045 + 1;
      return a * n;
    };
    let i = 0;
    for (const id in cur) {
      let v = wob(id, i++);
      if (id === "PARAM_MOUTH_FORM" && speaking)
        v = Math.max(v, cur[id] * .75) + .12 * Math.abs(Math.sin(now / 110));
      if (id === "PARAM_MOUTH_OPEN_Y" && speaking) continue; // lip-sync owns it
      try { cm.setParameterValueById(id, v); } catch (e) {}
    }
    /* breath when we own the face (idle motion suspended) */
    if (activeName !== "neutral") {
      try { cm.setParameterValueById("PARAM_BREATH", .5 + .5 * Math.sin(now / 2400)); } catch (e) {}
    }
  }

  /* ---------- public ---------- */
  return {
    init(m) {
      model = m;
      collect(Object.keys(PRESETS).flatMap(k => Object.keys(PRESETS[k])));
      nextMicro = performance.now() + 4000;
      if (!raf) raf = requestAnimationFrame(tick);
      if (pending) { const p = pending; pending = null; setEmotion(p[0], p[1]); }
    },
    react(text, holdMs) {
      const emo = detect(text);
      if (!model) { pending = [emo, holdMs]; return emo; }
      setEmotion(emo, holdMs || 4200 + Math.random() * 3000);
      return emo;
    },
    hear(text) {
      const emo = detect(text);
      if (emo === "neutral" || emo === "thinking") return emo;
      if (!model) { pending = [emo, 2200]; return emo; }
      setEmotion(emo === "angry" ? "sad" : emo, 2200);  // user angry → she goes sorry-face
      return emo;
    },
    set(name, holdMs) {
      if (!model) { pending = [name, holdMs]; return; }
      setEmotion(name, holdMs);
    },
    poke() { this.set("happy", 2400); playMotion("Tap"); },
    speaking(v) { speaking = !!v; },
    get emotion() { return activeName; }
  };
})();
