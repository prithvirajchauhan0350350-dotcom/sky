/* SKY IRON OS — HUD logic (chat, voice-reactive waves, telemetry, boot) */
"use strict";
const $ = id => document.getElementById(id);
const chat = $("chat");
let audioCtx = null, analyser = null, waveData = null;
let pollTimer = null;
let state = "boot";   // boot | online | thinking | speaking

/* ---------- state ---------- */
function setState(s){
  state = s;
  $("state").textContent = s.toUpperCase();
  $("state").className = "st-" + s;
  $("avstate").textContent = s.toUpperCase();
  document.body.classList.toggle("speaking", s === "speaking");
  document.body.classList.toggle("thinking", s === "thinking");
}

function esc(s){ const d = document.createElement("div"); d.textContent = s; return d.innerHTML; }

function bub(who, text){
  const row = document.createElement("div");
  row.className = "row " + (who === "me" ? "me" : "sky");
  const tag = document.createElement("span");
  tag.className = "who"; tag.textContent = who === "me" ? "YOU ▸" : "SKY ▸";
  const body = document.createElement("span");
  body.className = "msg"; body.textContent = text;
  row.append(tag, body); chat.append(row);
  chat.scrollTop = chat.scrollHeight;
  blip(who === "me" ? 880 : 660, .05);
  return body;
}

/* ---------- UI blips (after first user gesture only) ---------- */
function blip(freq, dur){
  try{
    if(!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const o = audioCtx.createOscillator(), g = audioCtx.createGain();
    o.type = "sine"; o.frequency.value = freq; g.gain.value = .028;
    o.connect(g); g.connect(audioCtx.destination);
    o.start(); o.stop(audioCtx.currentTime + dur);
  }catch(e){}
}

/* ---------- voice-reactive circular waveform ---------- */
function driveMouth(talking){
  if(!l2dModel) return;
  let v = 0;
  if (talking && waveData){
    let sum = 0, n = Math.min(10, waveData.length);
    for (let i = 0; i < n; i++) sum += waveData[i];
    v = Math.min(1, (sum/n)/140);
    v = .12 + v * .88;
  } else if (state === "speaking"){
    v = .35 + .3 * Math.abs(Math.sin(Date.now()/90));
  }
  try{
    const cm = l2dModel.internalModel.coreModel;
    cm.setParameterValueById("PARAM_MOUTH_OPEN_Y", v);
    if (talking){
      /* she "speaks with her body": head bob + sway driven by voice energy */
      const t = Date.now(), s1 = Math.sin(t/180), s2 = Math.sin(t/310 + 1.2), s3 = Math.sin(t/430);
      cm.setParameterValueById("PARAM_ANGLE_Y", (4.5*s1) * (0.4 + v*0.6));
      cm.setParameterValueById("PARAM_ANGLE_Z", (3.5*s2) * (0.5 + v*0.5));
      cm.setParameterValueById("PARAM_BODY_ANGLE_X", (5*s3) * (0.5 + v*0.5));
      cm.setParameterValueById("PARAM_BODY_ANGLE_Y", (3*s1) * (0.5 + v*0.5));
    }
  }catch(e){}
}
function drawWave(){
  requestAnimationFrame(drawWave);
  const c = $("wave"), x = c.getContext("2d");
  x.clearRect(0, 0, c.width, c.height);
  const cx = c.width/2, cy = c.height/2, R = 205, n = 96;
  const talking = state === "speaking" && analyser;
  const col = state === "speaking" ? [251,191,36] : [34,211,238];
  if (talking) analyser.getByteFrequencyData(waveData);
  driveMouth(talking);
  x.lineWidth = 2.2;
  for(let i = 0; i < n; i++){
    const a = i/n * Math.PI*2 - Math.PI/2;
    let v;
    if (talking){
      v = waveData[(i * waveData.length / n) | 0] / 255;
      v = Math.pow(v, 1.4) * 1.6;
    } else if (state === "thinking"){
      v = .10 + .08 * Math.abs(Math.sin(Date.now()/160 + i*.7));
    } else {
      v = .05 + .045 * Math.sin(Date.now()/700 + i*.55);
    }
    const len = R*.10 + Math.min(v,1.3) * R*.42;
    const r0 = R*1.0, r1 = r0 + len;
    x.strokeStyle = `rgba(${col[0]},${col[1]},${col[2]},${.22 + Math.min(v,1)*.65})`;
    x.beginPath();
    x.moveTo(cx + Math.cos(a)*r0, cy + Math.sin(a)*r0);
    x.lineTo(cx + Math.cos(a)*r1, cy + Math.sin(a)*r1);
    x.stroke();
  }
  /* inner shimmer ring */
  x.lineWidth = 1;
  x.strokeStyle = `rgba(${col[0]},${col[1]},${col[2]},.16)`;
  x.beginPath(); x.arc(cx, cy, R*.99, 0, Math.PI*2); x.stroke();
}

/* ---------- TTS with analyser ---------- */
function wireAnalyser(a){
  try{
    if(!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    if(audioCtx.resume) audioCtx.resume();
    const src = audioCtx.createMediaElementSource(a);
    analyser = audioCtx.createAnalyser();
    analyser.fftSize = 128;
    waveData = new Uint8Array(analyser.frequencyBinCount);
    src.connect(analyser); analyser.connect(audioCtx.destination);
  }catch(e){ analyser = null; }
}

function splitSay(text){
  const parts = text.match(/[^।!?\.]+[।!?\.]*/g) || [text];
  const out = []; let buf = "";
  for (const p of parts){
    if (buf && (buf + p).length > 220){ out.push(buf.trim()); buf = p; }
    else buf += p;
  }
  if (buf.trim()) out.push(buf.trim());
  return out.length ? out : [text];
}

async function say(text){
  if(!text) return;
  setState("speaking");
  SkyFace.speaking(true);
  const end = () => { setState("online"); SkyFace.speaking(false); };
  try{
    /* all sentence mp3s fetched in parallel; played in order —
       first sentence starts ~1-2s after text instead of waiting for the whole reply */
    const chunks = splitSay(text).slice(0, 6);
    const jobs = chunks.map(c =>
      fetch("/api/tts?text=" + encodeURIComponent(c.slice(0, 500)))
        .then(r => r.ok ? r.blob() : Promise.reject())
        .catch(() => null));
    let q = Promise.resolve();
    for (const j of jobs){
      q = q.then(() => j).then(b => b ? new Promise(res => {
        const a = new Audio(URL.createObjectURL(b));
        wireAnalyser(a);
        a.onended = a.onerror = res;
        a.play().catch(res);
      }) : null);
    }
    await q;
    end();
  }catch(e){
    try{
      const u = new SpeechSynthesisUtterance(text);
      u.rate = 1.02; u.onend = end;
      speechSynthesis.speak(u);
    }catch(e2){ end(); }
  }
}

/* ---------- chat ---------- */
function startConfirmPoll(){
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try{
      const p = await (await fetch("/api/pending")).json();
      const bar = $("confirmbar");
      if (p.pending && p.pending.length){
        const c = p.pending[p.pending.length - 1];
        bar.style.display = "flex";
        $("cq").textContent = "⚠ SKY asks permission: " + c.cmd;
        bar.dataset.id = c.id;
      } else bar.style.display = "none";
    }catch(e){}
  }, 1500);
}
$("cy").onclick = () => decide(true);
$("cn").onclick = () => decide(false);
function decide(ans){
  const bar = $("confirmbar");
  fetch("/api/confirm", {method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({id: bar.dataset.id, answer: ans})});
  bar.style.display = "none";
}

$("f").onsubmit = async e => {
  e.preventDefault();
  const inp = $("inp"), text = inp.value.trim();
  if(!text) return;
  inp.value = "";
  bub("me", text);
  SkyFace.hear(text);
  if (await obeyMove(text)) return;   /* movement order — LLM skip */
  const t = bub("sky", "processing, sir…");
  setState("thinking");
  $("confirmbar").style.display = "none";
  startConfirmPoll();
  try{
    const r = await fetch("/api/chat", {method:"POST",
      headers:{"Content-Type":"application/json"}, body: JSON.stringify({text})});
    const j = await r.json();
    t.textContent = j.reply || ("! " + (j.error || "no reply"));
    SkyFace.react(j.reply || "");
    say(j.spoken || j.reply || "");
  }catch(err){ t.textContent = "! hub unreachable"; setState("online"); }
  clearInterval(pollTimer);
  $("confirmbar").style.display = "none";
};

/* ---------- mic ---------- */
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
$("mic").onclick = () => {
  if(!SR){ $("inp").placeholder = "mic needs Edge/Chrome"; return; }
  const r = new SR(); r.lang = "en-IN"; r.interimResults = false;
  $("mic").classList.add("rec");
  r.onresult = e => { $("inp").value = e.results[0][0].transcript;
    $("mic").classList.remove("rec"); $("f").requestSubmit(); };
  r.onerror = r.onend = () => $("mic").classList.remove("rec");
  r.start();
};

/* ---------- telemetry ---------- */
function gaugeColor(v){ return v < 60 ? [34,211,238] : v < 85 ? [251,191,36] : [248,113,113]; }
function setGauge(k, pct){
  const c = document.querySelector(`.gc[data-k="${k}"]`), x = c.getContext("2d");
  const w = c.width, cx = w/2, cy = w/2, r = w/2 - 10;
  x.clearRect(0, 0, w, w);
  const a0 = Math.PI * .75, a1 = Math.PI * 2.25;
  x.lineWidth = 6; x.lineCap = "round";
  x.strokeStyle = "rgba(34,211,238,.12)";
  x.beginPath(); x.arc(cx, cy, r, a0, a1); x.stroke();
  const p = Math.max(0, Math.min(100, pct || 0)) / 100;
  const col = gaugeColor(pct || 0);
  x.strokeStyle = `rgb(${col[0]},${col[1]},${col[2]})`;
  x.shadowColor = x.strokeStyle; x.shadowBlur = 8;
  x.beginPath(); x.arc(cx, cy, r, a0, a0 + (a1 - a0) * p); x.stroke();
  x.shadowBlur = 0;
  x.fillStyle = "#d7f5ff"; x.font = "600 20px Consolas"; x.textAlign = "center";
  x.fillText(Math.round(pct || 0) + "%", cx, cy + 2);
}

let lastFeed = 0;
function feed(line, warn){
  const el = document.createElement("div");
  if (line && line.warn) line = line.line;
  const t = new Date().toTimeString().slice(0, 8);
  el.textContent = `[${t}] ${line}`;
  if (line.indexOf("WARN") >= 0) el.className = "warn";
  $("feed").prepend(el);
  while ($("feed").children.length > 9) $("feed").lastChild.remove();
}

async function refreshVitals(){
  try{
    const v = await (await fetch("/api/vitals")).json();
    const s = v.sys || {};
    setGauge("cpu", s.cpu_percent); setGauge("ram", s.ram_percent); setGauge("disk", s.disk_percent);
    $("vitals").textContent = v.report || "—";
    const warn = (s.cpu_percent > 85 || s.ram_percent > 85) ? " · WARN" : "";
    feed(`SYS CPU ${Math.round(s.cpu_percent||0)}% · MEM ${Math.round(s.ram_percent||0)}% · DISK ${Math.round(s.disk_percent||0)}%${warn ? "" : " NOMINAL"}`);
  }catch(e){ $("vitals").textContent = "telemetry offline"; }
}

async function refreshHealth(){
  try{
    const h = await (await fetch("/api/health")).json();
    $("led-ear").className = "dot " + (h.daemons && h.daemons.skyear ? "on" : "off");
    $("led-skyd").className = "dot " + (h.daemons && h.daemons.skyd ? "on" : "off");
    $("led-brain").className = "dot " + (h.brain ? "on" : "off");
  }catch(e){
    $("led-ear").className = $("led-skyd").className = $("led-brain").className = "dot off";
  }
}

async function refreshReminders(){
  try{
    const r = await (await fetch("/api/reminders")).json();
    const rows = (r.reminders || []).map(x => {
      const d = new Date(x.due * 1000).toLocaleString([],
        {day:"2-digit", month:"short", hour:"2-digit", minute:"2-digit"});
      return `<div class='rem'><b>#${x.id} · ${d}</b>${esc(x.text)}</div>`;
    });
    $("rems").innerHTML = rows.join("") || "<span class='none'>no scheduled tasks</span>";
  }catch(e){}
}

/* ---------- clock ---------- */
function tick(){
  const d = new Date();
  $("clock").textContent = d.toTimeString().slice(0, 8);
  $("date").textContent = d.toLocaleDateString([], {weekday:"short", day:"2-digit",
    month:"short", year:"numeric"}).toUpperCase();
}

/* ---------- Live2D hologram girl + free-roam engine ---------- */
let l2dModel = null, l2dApp = null, l2dWrap = null;
async function initLive2D(){
  try{
    if(!window.PIXI || !PIXI.live2d || !PIXI.live2d.Live2DModel)
      throw new Error("live2d libs missing");
    const host = $("holo");
    const app = new PIXI.Application({
      view: $("l2d"), backgroundAlpha: 0, antialias: true,
      autoDensity: true, resolution: Math.min(2, window.devicePixelRatio || 1),
      width: host.clientWidth || 900, height: host.clientHeight || 700,
      resizeTo: host
    });
    const model = await PIXI.live2d.Live2DModel.from(
      "/live2d/shizuku/runtime/shizuku.model3.json");
    l2dModel = model;
    l2dApp = app;
    l2dWrap = new PIXI.Container();   /* roam moves the wrap; model stays home inside it */
    app.stage.addChild(l2dWrap);
    l2dWrap.addChild(model);
    const fit = () => {
      const w = app.screen.width, h = app.screen.height;
      model.scale.set(1, 1);                  /* unscaled measure — resize par scale compound na ho */
      const uw = model.width, uh = model.height;
      let s, px, py;
      const v = roam.uvis;
      if (v){                                  /* naapa hua VISUAL body (canvas quad nahi) */
        s = Math.min((h * .72) / v.h, (w * .36) / v.w);
        px = w / 2 - (v.lx + v.w / 2) * s;    /* visual centre-x → w/2 */
        py = (h - 110) - (v.ty + v.h) * s;    /* visual feet → h-110 */
        roam.gw = v.w * s; roam.gh = v.h * s;
      } else {                                 /* boot fallback: canvas quad */
        s = Math.min((h * .74) / uh, (w * .34) / uw);
        px = w / 2; py = h - 110;
        roam.gw = uw * s; roam.gh = uh * s;
      }
      roam.baseScale = s;
      roam.homeX = px; roam.homeY = py;
      model.anchor.set(0, 0);                 /* position = measurement reference point */
      model.scale.set(s, s);                  /* positive only — negative scale Cubism render todta hai */
      model.position.set(px, py);
    };
    /* visual-bounds calibration: Cubism character canvas quad se bahar draw hota hai —
       chhota karke centre pe render karke ACTUAL pixel bounds naapo, phir fit() dobara */
    const calibrate = () => {
      try{
        const ex = app.renderer.plugins && app.renderer.plugins.extract;
        if (!ex || !l2dModel) return;
        const res = app.renderer.resolution || 1;
        const W = app.screen.width, H = app.screen.height;
        const sMeas = roam.baseScale * .12;
        l2dModel.scale.set(sMeas, sMeas);
        l2dModel.position.set(W / 2 - l2dWrap.position.x, H / 2 - l2dWrap.position.y);
        app.render();
        const c = ex.canvas(l2dWrap);
        const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
        let x0 = c.width, x1 = -1, y0 = c.height, y1 = -1;
        for (let y = 0; y < c.height; y += 2) for (let x = 0; x < c.width; x += 2){
          if (d[(y * c.width + x) * 4 + 3] > 40){
            if (x < x0) x0 = x; if (x > x1) x1 = x;
            if (y < y0) y0 = y; if (y > y1) y1 = y;
          }
        }
        if (x1 <= x0 || y1 <= y0) return;
        roam.uvis = { w: (x1 - x0) / res / sMeas, h: (y1 - y0) / res / sMeas,
                      lx: (x0 / res - W / 2) / sMeas, ty: (y0 / res - H / 2) / sMeas };
        fit();
      }catch(e){}
    };
    fit();
    setTimeout(calibrate, 1500);            /* greeting ke baad ek baar actual body naap lo */
    window.addEventListener("resize", () => {
      fit();
      if (l2dWrap){
        /* feet-space clamp: feet = wrap + home */
        l2dWrap.position.x = clampFx(l2dWrap.position.x + roam.homeX, innerWidth) - roam.homeX;
        l2dWrap.position.y = clampFy(l2dWrap.position.y + roam.homeY, innerHeight) - roam.homeY;
      }
    });
    try{ model.motion("Idle"); }catch(e){}
    /* human-like gestures: arm/head wave — jab tak roam/hold nahi, har ~5s random motion */
    const GESTURES = ["Tap", "FlickUp", "Flick3"];
    setInterval(() => {
      if (!l2dModel || roam.hold || roam.hover) return;
      if (Math.random() < .6){
        try{ l2dModel.motion(GESTURES[(Math.random() * GESTURES.length) | 0], undefined, 3); }catch(e){}
      }
    }, 5200);
    SkyFace.init(model);
    feed("EMOTION ENGINE ONLINE — human expressions linked");
    $("stage").addEventListener("click", () => {
      try{ if (l2dModel) l2dModel.motion("Tap"); }catch(e){}
      SkyFace.poke();
    });
    feed("HOLOGRAM PROJECTION ONLINE — Live2D core linked");
    initRoam();
    feed("FREE-ROAM ENGINE ONLINE — full-floor access granted");
  }catch(e){
    $("holo-fallback").style.display = "block";
    feed("WARN hologram offline — static portrait (" + ((e && e.message) || e) + ")");
  }
}

/* ---------- free-roam: girl walks the whole screen ---------- */
const roam = { on:false, hold:false, hover:false, dir:1, tx:null, ty:null,
               dwellUntil:0, frozenUntil:0, dwellMs:18000,
               hoverSince:0, baseScale:1, gw:300, gh:600, homeX:0, homeY:0, bubble:null, hit:null };
const CHATTER = [
  "sir, yahan se pura floor monitor kar rahi hu",
  "diagnostics scan kar li — sab nominal hai",
  "sir, yahan ki hawa achhi hai",
  "grid stable hai, koi anomaly nahi",
  "sir, boliye agar kahin jaana ho"
];

function clampFx(x, W){ const m = roam.gw * .55 + 6; return Math.max(m, Math.min(W - m, x)); }
function clampFy(y, H){ const top = roam.gh + 8; return Math.max(top, Math.min(H - 110, y)); }

function initRoam(){
  const hit = document.createElement("div"); hit.id = "skyhit";
  const b = document.createElement("div"); b.id = "skybubble";
  document.body.append(hit, b);
  roam.hit = hit; roam.bubble = b;
  hit.addEventListener("mouseenter", () => { roam.hover = true; roam.hoverSince = Date.now(); });
  hit.addEventListener("mouseleave", () => { roam.hover = false; });
  hit.addEventListener("click", () => {
    try{ if (l2dModel) l2dModel.motion("Tap"); }catch(e){}
    SkyFace.poke();
  });
  roam.on = true;
  roam.dwellUntil = Date.now() + 9000;   /* greeting ke baad roaming start */
  setInterval(roamTick, 50);
}

function roamTick(){
  if (!l2dModel || !l2dWrap || !roam.on) return;
  const now = Date.now(), W = innerWidth, H = innerHeight;
  const feetX = l2dWrap.position.x + roam.homeX;
  const feetY = l2dWrap.position.y + roam.homeY;
  /* hit-proxy + bubble girl ke saath chalte hain */
  roam.hit.style.left = Math.round(feetX - roam.gw / 2) + "px";
  roam.hit.style.top  = Math.round(Math.max(0, feetY - roam.gh * .82)) + "px";
  roam.hit.style.width  = Math.round(roam.gw) + "px";
  roam.hit.style.height = Math.round(roam.gh * .82) + "px";
  roam.bubble.style.left = Math.max(8, Math.min(W - 264, feetX - 30)) + "px";
  roam.bubble.style.top  = Math.max(6, feetY - roam.gh - 46) + "px";

  /* pet-pause: cursor upar ho to 3.5s rukti hai, phir khud nikal jaati hai */
  if (roam.hold || (roam.hover && now - roam.hoverSince < 3500) || now < roam.frozenUntil) return;

  if (roam.tx != null){
    const dx = roam.tx - feetX, dy = roam.ty - feetY;
    const dist = Math.hypot(dx, dy), step = 7.5;
    if (dist <= step){
      l2dWrap.position.set(roam.tx - roam.homeX, roam.ty - roam.homeY);
      roam.tx = roam.ty = null;
      roam.dwellUntil = now + roam.dwellMs;
      if (Math.random() < .30) girlBubble(CHATTER[(Math.random() * CHATTER.length) | 0]);
    } else {
      if (Math.abs(dx) > 2) roam.dir = dx > 0 ? 1 : -1;
      l2dWrap.position.set(
        l2dWrap.position.x + dx / dist * step,
        l2dWrap.position.y + dy / dist * step);
    }
  } else if (now >= roam.dwellUntil){
    roam.tx = clampFx(40 + Math.random() * (W - 80), W);
    roam.ty = clampFy(H * .44 + Math.random() * (H * .52), H);
    roam.dwellMs = 14000 + Math.random() * 16000;
  }
  /* facing flip + walk bob */
  const bob = (roam.tx != null) ? Math.abs(Math.sin(now / 130)) * 7 : Math.sin(now / 850) * 2.2;
  try{
    l2dModel.scale.set(roam.baseScale, roam.baseScale);   /* hamesha positive — flip render garbage karta hai */
    l2dModel.position.set(roam.homeX, roam.homeY - bob);
  }catch(e){}
}

let bubbleTimer = 0;
function girlBubble(text){
  if (!roam.bubble) return;
  roam.bubble.textContent = text;
  roam.bubble.classList.add("show");
  clearTimeout(bubbleTimer);
  bubbleTimer = setTimeout(() => roam.bubble.classList.remove("show"), 3800);
}
function moveSay(text, speak){
  girlBubble(text);
  bub("sky", text);
  SkyFace.react(text);
  if (speak) say(text);
}

/* ---------- movement orders (Hinglish / English) ---------- */
const RX = {
  neg:    /\b(mat|nahin|nahi|not|dont|don't|never)\b/,
  stop:   /\b(ruk\s?jao|rukja|ruko|rukna|ruki|stop|stand\s?still)\b/,
  hold:   /\b(ghumna\s+band|ghoomna\s+band|roam\w*\s+band|movement\s+band|ghumna\s+chhod|stop\s+roam\w*)\b/,
  unhold: /\b(ghumo|ghoomo|ghum\w*\s+raho|ghumna\s+(shuru|start|chalu)|ghoomna\s+(shuru|start|chalu)|roam\w*\s+(again|shuru|free|karo|on)|wander)\b/,
  here:   /\b(idhar|idher|yahan|yaha|here|mere\s+paas|mere\s+pass|meri\s+taraf)\b/,
  left:   /\b(left|bayen|baye|baen|baayen)\b/,
  right:  /\b(right|dahine|dayen|daayne)\b/,
  top:    /\b(upar|oopar|up|top|upper)\b/,
  down:   /\b(neeche|niche|down|bottom)\b/,
  center: /\b(center|centre|beech|middle)\b/,
  corner: /\b(corner|kona|kone|kinare)\b/,
  dwell:  /\b(raho|rehna|thehro|theharna|wait)\b/,
  verb:   /\b(jao|jaao|ja\b|go|move|shift|hato|chalo|chal\b|aao|come|ghumo|ghoomo|ghumna|ghoomna|roam|wander|raho|rehna|thehro|jump)\b/
};

function parseMove(raw){
  if(!raw) return null;
  const t = (" " + String(raw).toLowerCase() + " ").replace(/\s+/g, " ");
  const has = r => r.test(t);
  if (has(RX.neg)) return null;
  if (has(RX.stop)) return { stop: 1 };
  if (has(RX.hold)) return { hold: 1 };
  if (has(RX.unhold)) return { unhold: 1 };
  const verb = has(RX.verb);
  const here = has(RX.here);
  const d = { left: has(RX.left), right: has(RX.right), top: has(RX.top),
              down: has(RX.down), center: has(RX.center), corner: has(RX.corner) };
  const anyDir = d.left || d.right || d.top || d.down || d.center || d.corner;
  if (!verb && !here) return null;
  if (here && !anyDir) return { here: 1, dwell: has(RX.dwell) ? 1 : 0 };
  if (!anyDir) return has(RX.dwell) ? { dwell: 1 } : null;
  const o = { dwell: has(RX.dwell) ? 1 : 0 };
  if (d.left) o.x = "left"; else if (d.right) o.x = "right";
  if (d.top) o.y = "top"; else if (d.down) o.y = "down";
  if (d.center){ if (!o.x) o.x = "center"; if (!o.y) o.y = "center"; }
  if (d.corner){ if (!o.x) o.x = "right"; if (!o.y) o.y = "down"; }
  return o;
}

async function obeyMove(text){
  const m = parseMove(text);
  if (!m) return false;
  if (m.stop){ roam.frozenUntil = Date.now() + 30000; moveSay("Thehar gayi, sir."); return true; }
  if (m.hold){ roam.hold = true; moveSay("Roaming band, sir — boliye to wapas ghoomungi."); return true; }
  if (m.unhold){
    roam.hold = false; roam.frozenUntil = 0; roam.dwellUntil = 0; roam.tx = roam.ty = null;
    moveSay("Ghoomna shuru, sir!"); return true;
  }
  if (m.dwell && !m.x && !m.y && !m.here){
    roam.dwellUntil = Date.now() + 90000; moveSay("Yahin khadi hu, sir."); return true;
  }
  let tx = null, ty = null;
  roam.dwellMs = m.dwell ? 90000 : 25000;
  if (m.here){
    const c = await cursorPos();
    tx = c.x; ty = c.y + roam.gh * .45;   /* body cursor ke paas, feet neeche */
    roam.dwellMs = m.dwell ? 90000 : 45000;
    moveSay("Ji sir, aa rahi hu!");
  } else {
    if (m.x === "left") tx = roam.gw * .55 + 10;
    else if (m.x === "right") tx = innerWidth - roam.gw * .55 - 10;
    else if (m.x === "center") tx = innerWidth / 2;
    if (m.y === "top") ty = innerHeight * .44;
    else if (m.y === "down") ty = innerHeight - 112;
    else if (m.y === "center") ty = innerHeight * .62;
    moveSay(m.x ? ("Chali " + (m.x === "left" ? "left" : "right") + ", sir!") : "Ji sir, aa rahi hu!");
  }
  if (ty == null) ty = l2dWrap ? l2dWrap.position.y + roam.homeY : innerHeight * .8;
  if (tx == null) tx = l2dWrap ? l2dWrap.position.x + roam.homeX : innerWidth / 2;
  roam.tx = clampFx(tx, innerWidth);
  roam.ty = clampFy(ty, innerHeight);
  return true;
}

let lastMouse = { x: innerWidth / 2, y: innerHeight / 2 };
addEventListener("mousemove", e => { lastMouse.x = e.clientX; lastMouse.y = e.clientY; });
async function cursorPos(){
  const a = pyApi();
  if (a && a.come_here){
    try{
      const p = await a.come_here();
      if (p && typeof p.x === "number" && p.x >= 0){
        const k = window.devicePixelRatio || 1;
        return { x: p.x / k, y: p.y / k };
      }
    }catch(e){}
  }
  return { x: lastMouse.x, y: lastMouse.y };
}

/* ---------- agency roster (specialist personas) ---------- */
const agency = { divs: [], agents: [], active: null, div: "all", q: "", cur: null };

async function refreshAgency(){
  try{
    const j = await (await fetch("/api/agency")).json();
    agency.divs = j.divisions || []; agency.agents = j.agents || [];
    agency.active = j.active || null;
    $("agency-count").textContent = j.total || agency.agents.length;
    renderAgencyTabs(); renderAgencyList(); renderAgencyActive();
    feed("AGENCY ROSTER ONLINE — " + (j.total || 0) + " specialists linked");
  }catch(e){ feed("WARN agency roster offline"); }
}
function renderAgencyActive(){
  const on = agency.active;
  $("agency-active").classList.toggle("hidden", !on);
  document.body.classList.toggle("active-specialist", !!on);
  if (on) $("agency-active-name").textContent = "ACTIVE: " + on.name.toUpperCase();
}
function renderAgencyTabs(){
  const t = $("agency-tabs"); t.innerHTML = "";
  const mk = (id, label, color, n) => {
    const b = document.createElement("button");
    b.textContent = label.toUpperCase() + " ·" + n;
    if (agency.div === id){ b.className = "sel"; b.style.background = color; }
    b.onclick = () => { agency.div = id; renderAgencyTabs(); renderAgencyList(); };
    t.append(b);
  };
  mk("all", "all", "rgba(34,211,238,.85)", agency.agents.length);
  for (const d of agency.divs) mk(d.id, d.label, d.color, d.count);
}
function renderAgencyList(){
  const divBy = Object.fromEntries(agency.divs.map(d => [d.id, d]));
  const q = agency.q.toLowerCase();
  const rows = agency.agents.filter(a =>
    (agency.div === "all" || a.division === agency.div) &&
    (!q || (a.name + " " + a.description + " " + a.division).toLowerCase().includes(q)));
  const L = $("agency-list"); L.innerHTML = "";
  if (!rows.length){ L.innerHTML = "<div class='ag-empty'>no specialist matches</div>"; return; }
  for (const a of rows){
    const r = document.createElement("div"); r.className = "ag-row";
    const em = document.createElement("span"); em.className = "em"; em.textContent = a.emoji || "◆";
    const nm = document.createElement("span"); nm.className = "nm"; nm.textContent = a.name;
    const dv = document.createElement("span"); dv.className = "dv";
    const d = divBy[a.division] || {};
    dv.textContent = (d.label || a.division).toUpperCase();
    dv.style.color = d.color || "var(--cy)";
    dv.style.borderColor = (d.color || "#22d3ee") + "88";
    r.append(em, nm, dv);
    r.onclick = () => openAgencyAgent(a.slug);
    L.append(r);
  }
}
async function openAgencyAgent(slug){
  try{
    const j = await (await fetch("/api/agency/agent?slug=" + encodeURIComponent(slug))).json();
    if (j.error){ feed("WARN agency: " + j.error); return; }
    agency.cur = slug;
    const head = $("agency-detail-head"); head.innerHTML = "";
    const t = document.createElement("div"); t.className = "t";
    t.textContent = (j.meta.emoji || "") + " " + j.meta.name;
    const d = document.createElement("div"); d.className = "d";
    d.textContent = j.meta.description + (j.meta.vibe ? "  —  " + j.meta.vibe : "");
    head.append(t, d);
    $("agency-detail-body").textContent = j.body;
    $("agency-list").classList.add("hidden");
    $("agency-q").classList.add("hidden");
    $("agency-tabs").classList.add("hidden");
    $("agency-active").classList.add("hidden");
    $("agency-detail").classList.remove("hidden");
    $("agency-activate").textContent = (agency.active && agency.active.slug === slug)
      ? "✓ ACTIVE" : "⚡ ACTIVATE";
    $("agency-detail-body").scrollTop = 0;
  }catch(e){ feed("WARN agency detail failed"); }
}
function agencyRosterView(){
  $("agency-detail").classList.add("hidden");
  $("agency-list").classList.remove("hidden");
  $("agency-q").classList.remove("hidden");
  $("agency-tabs").classList.remove("hidden");
  renderAgencyActive();
  agency.cur = null;
}
$("agency-btn").onclick = () => {
  const a = $("agency");
  a.classList.toggle("open");
  if (a.classList.contains("open")){ blip(980, .05); agencyRosterView(); }
};
$("agency-close").onclick = () => $("agency").classList.remove("open");
$("agency-q").oninput = e => { agency.q = e.target.value.trim(); renderAgencyList(); };
$("agency-back").onclick = agencyRosterView;
$("agency-activate").onclick = async () => {
  if (!agency.cur) return;
  try{
    const r = await fetch("/api/agency/activate", {method:"POST",
      headers:{"Content-Type":"application/json"}, body: JSON.stringify({slug: agency.cur})});
    const j = await r.json();
    if (j.error){ feed("WARN agency activate: " + j.error); return; }
    agency.active = j.active;
    renderAgencyActive();
    $("agency-activate").textContent = "✓ ACTIVE";
    feed("SPECIALIST ACTIVE — " + j.active.name.toUpperCase() + " (" + j.active.division + ")");
    const msg = "Specialist activated, sir: " + j.active.name + ".";
    bub("sky", msg); say(msg);
  }catch(e){ feed("WARN agency activate failed"); }
};
$("agency-deact").onclick = async () => {
  try{
    await fetch("/api/agency/deactivate", {method:"POST"});
    agency.active = null;
    feed("SPECIALIST STOOD DOWN — plain SKY restored");
    const msg = "Standing down, sir — back to plain SKY.";
    bub("sky", msg); say(msg);
    agencyRosterView();
  }catch(e){}
};

/* ---------- boot sequence ---------- */
const BOOT = [
  "SKY OS v3.0 — SECURE BOOT",
  "PERSONA MATRIX .................... OK",
  "MEMORY CORE ....................... OK",
  "AGENCY ROSTER ..................... OK",
  "VOICE SYNTH · SWARA / NEERJA ...... OK",
  "WAKE-WORD ENGINE .................. OK",
  "NEURAL LINK ....................... OK",
  "HOLOGRAM PROJECTION ............... OK",
  "FREE-ROAM ENGINE .................. OK",
  "ALL SYSTEMS NOMINAL"
];
let bootDone = false;
function finishBoot(){
  if (bootDone) return;
  bootDone = true;
  $("boot").classList.add("gone");
  setState("online");
  initLive2D();
  refreshVitals(); refreshHealth(); refreshReminders();
  refreshAgency();
  const h = new Date().getHours();
  const hi = h < 5 ? "Working late, sir?" : h < 12 ? "Good morning, sir"
           : h < 17 ? "Good afternoon, sir" : "Good evening, sir";
  const greet = hi + ". All systems are online — chat, voice, memory and my hologram. How may I assist?";
  bub("sky", greet);
  say(greet);
  SkyFace.set("happy", 6000);
  try{ $("inp").focus(); }catch(e){}
}
function runBoot(){
  const log = $("bootlog"), bar = $("bootbar").firstElementChild;
  let i = 0;
  const step = () => {
    if (bootDone) return;
    if (i < BOOT.length){
      log.textContent += (i ? "\n" : "") + "> " + BOOT[i];
      bar.style.width = Math.round((i + 1) / BOOT.length * 100) + "%";
      i++;
      setTimeout(step, i === 1 ? 650 : 330);
    } else {
      setTimeout(finishBoot, 550);
    }
  };
  step();
}
$("boot").onclick = finishBoot;
document.addEventListener("keydown", e => { if (state === "boot") finishBoot(); }, {once:false});

/* ---------- native window hooks (pywebview) ---------- */
function pyApi(){ return window.pywebview && window.pywebview.api; }
document.addEventListener("keydown", e => {
  if (e.key === "Escape"){
    if ($("agency").classList.contains("open")){ $("agency").classList.remove("open"); return; }
    const a = pyApi();
    if (a && a.quit) a.quit();
  }
  if (e.key === "F11"){
    e.preventDefault();
    const a = pyApi();
    if (a && a.toggle_fs) a.toggle_fs();
    else if (document.fullscreenElement) document.exitFullscreen();
    else document.documentElement.requestFullscreen();
  }
});

/* ---------- go ---------- */
tick(); setInterval(tick, 500);
drawWave();
refreshHealth(); setInterval(refreshHealth, 10000);
setInterval(refreshVitals, 3000);
setInterval(refreshReminders, 30000);
runBoot();
