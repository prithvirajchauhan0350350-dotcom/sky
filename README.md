     1|# SKY — personal AI assistant (JARVIS-style)
     2|
     3|One program, one home: **`C:\Users\Svelt\SKY`**, running on Windows Python 3.14
     4|(venv at `.venv`). Nothing runs from WSL any more.
     5|
     6|Brain: **glm-5.3-flash via local OmniRoute** (`http://localhost:20128/v1`,
     7|OpenAI-compatible, native tool-calling). A second brain — Anthropic Claude —
     8|can be switched on in config.json (`brain.active` / `SKY_BRAIN` env).
     9|Keys live in `C:\Users\Svelt\SKY\.env` (`OMNIROUTE_API_KEY`, `CLAUDE_API_KEY`) —
    10|never printed, never committed.
    11|
    12|## Run
    13|
    14|```bat
    15|cd C:\Users\Svelt\SKY
    16|.venv\Scripts\python.exe sky.py                 interactive agent REPL (confirm gates)
    17|.venv\Scripts\python.exe sky.py --talk          voice mode: Enter = push-to-talk
    18|.venv\Scripts\python.exe sky.py --listen        always-on: say "hey sky", then speak
    19|.venv\Scripts\python.exe sky.py --hud           dashboard: live status, mic meter, chat
    20|.venv\Scripts\python.exe sky.py --ask "..."     one-shot agentic answer
    21|.venv\Scripts\python.exe sky.py --say "..."     speak one line
    22|.venv\Scripts\python.exe sky.py --see "q"       screenshot Windows desktop + OCR/vision
    23|.venv\Scripts\python.exe sky.py --remind "t" --at "in 10m"
    24|.venv\Scripts\python.exe sky.py --briefing      morning briefing now (speaks)
    25|.venv\Scripts\python.exe sky.py --facts|--reset|--wipe
    26|.venv\Scripts\python.exe skyd.py                daemon: reminders + daily 09:00 briefing
    27|```
    28|
    29|Desktop launchers (`Desktop\SKY\`): `Sky.bat` (REPL), `SkyTalk.bat` (voice),
    30|`SkyListen.bat` (wake word), `SkyHUD.bat` (dashboard).
    31|Autostart at logon: HKCU `...\CurrentVersion\Run` entries **SKY** (skyd.py)
    32|and **SKYEAR** (skyear.py) point at the same venv python.
    33|
    34|REPL commands: /exit /help /reset /facts /wipe /talk /listen /see [q] /status
    35|
    36|## Brain (config.json -> "brain")
    37|
    38|```json
    39|"brain": {
    40|  "active": "omni",
    41|  "profiles": {
    42|    "omni":   {"kind": "openai",    "base_url": "http://localhost:20128/v1",
    43|               "model": "glm-5.3-flash", "api_key_env": "OMNIROUTE_API_KEY"},
    44|    "claude": {"kind": "anthropic", "model": "claude-sonnet-5",
    45|               "api_key_env": "CLAUDE_API_KEY"}
    46|  }
    47|}
    48|```
    49|
    50|`SKY_BRAIN=claude` overrides for one run. core/llm.py normalizes both brains
    51|to the same message shape, so the 30+ tools work identically on either.
    52|
    53|## What's inside
    54|
    55|- **Memory** — SQLite `data/memory.db`: identity facts newest-wins, general
    56|  facts capped 100, rolling history 300, old turns auto-summarized.
    57|  Fact extraction: "my name is X", "mera naam X hai", "i live in X",
    58|  "i like X", "remember that X". Survives restarts.
    59|- **Voice** — edge-tts female Indian voices (en-IN-NeerjaNeural English,
    60|  hi-IN-SwaraNeural Hinglish/Hindi) → mp3 → Windows MCI playback (no ffmpeg
    61|  needed). Ears: faster-whisper small int8 on CPU. `pick_voice()` switches
    62|  per reply on Devanagari or romanized-Hinglish. Hologram hook: when the
    63|  hologram is running, SKY's speech is routed through it for lip sync.
    64|- **Hands** — 30+ native tools in core/agent.py: run_shell, read_file,
    65|  list_dir, launch_app, sys_report, web_fetch, web_search, read_page,
    66|  set_reminder, remember_fact, recall_memory, run_python (sandboxed),
    67|  type_text / click_at / press_key / get_screen_info / take_screenshot
    68|  (pyautogui, confirm-gated), vitals, run_diagnostics, analyze_data,
    69|  security_scan, scan_network, hash_file, encrypt_file, password_strength,
    70|  organize_folder, smart_home, check_knowledge, list_reminders,
    71|  delete_reminder, toggle_hologram, and more. Safety: hard-block regexes,
    72|  read-only allowlist runs free, everything else asks typed confirm in the
    73|  REPL (auto-denied in one-shot/HUD). Every action logged to
    74|  data/os_actions.log.
    75|- **Eyes** — PowerShell CopyFromScreen + System.Drawing downscale (no ffmpeg
    76|  on Windows) → vision model if `config.vision_model` set, else local OCR
    77|  (rapidocr). No vision credentials on the router yet → OCR path runs.
    78|- **Autonomy** — skyd daemon (APScheduler): reminders every 20s (spoken),
    79|  missed reminders on restart, daily briefing at `briefing_time`;
    80|  background learner (RSS/DDG) appends "learned:" facts.
    81|- **Wake word** — skyear.py always-on ear: say **"hey sky"** + command in one
    82|  breath; SKY acks, does it, speaks the result. The bundled openWakeWord
    83|  model is pretrained on the jarvis sound pattern (closest available audio
    84|  model); the transcript filter accepts hey sky/jarvis spellings alike.
    85|  20s open-mic follow-up window after each reply; risky commands confirmed
    86|  ALOUD; mic muted while SKY thinks/speaks.
    87|- **HUD** — `sky.py --hud`: Textual dashboard (brain up/down, skyd, cpu/ram/
    88|  disk, mic meter, chat box running the full agent).
    89|- **Hologram** — optional wireframe head bottom-right whose mouth follows
    90|  SKY's speech. Toggle with the `toggle_hologram` tool (or run
    91|  `hologram_app.py`); control API on 127.0.0.1:20129 (/say /hide /show /quit).
    92|
    93|## Files (C:\Users\Svelt\SKY)
    94|
    95|- sky.py (CLI/REPL) · skyd.py (daemon) · skyear.py (always-on ear) ·
    96|  sky_hud.py (dashboard) · hologram_app.py (hologram head + control API)
    97|- core/{llm,memory,persona,agent,voice,eyes,telemetry,wake,learner}.py
    98|- spy_avatar.py, spy_hologram.py — hologram rendering + lip-sync engine
    99|- spy_data.py, spy_sec.py, spy_ops.py, spy_vitals.py — Windows skill modules
   100|  (imported in-process by the agent)
   101|- automation.py — pyautogui hands (type/click/press)
   102|- persona.md — edit to retune personality · knowledge.txt — permanent facts
   103|- migrate_spy_memory.py — legacy Spy memory importer (idempotent)
   104|- config.json — brain/model/voice/briefing/vision/hologram settings
   105|- .env — OMNIROUTE_API_KEY / CLAUDE_API_KEY (secret; keep local)
   106|- data/memory.db · logs/sky.log · logs/skyd.log · data/os_actions.log
   107|- scripts/ — brain/vision/wakeword/whisper/voice probes · backup/ —
   108|  pre-merge backups of everything
   109|
   110|## Notes
   111|
   112|- Brain down => SKY says so and keeps running (degraded text mode).
   113|- GLM is a reasoning model: core/llm.py retries with 2x budget if thinking
   114|  ate the tokens.
   115|- OmniRoute runs in WSL on 0.0.0.0:20128 — Windows reaches it as
   116|  localhost:20128 (WSL2 localhost forwarding), no config needed.
   117|- All reminder/briefing times are local Windows time.
   118|

## Demo

> **user:** hey sky, what's eating my RAM?
> **sky:** Arre, Chrome alone is eating 3.2 GB, sir. Want me to close the tabs or just the ones playing ads?
>
> **user:** my api key is [redacted] pqr is my [redacted] bearer token.fake.tokenvalue12345
> **sky:** Sahi hai, noted. Also — I redact secrets from anything I export, so that line is safe to share.
>
> **user:** mera naam Prithvi hai
> **sky:** Achha sir, identity facts stay in memory.db — they never touch the card.

Secrets (API keys, bearer tokens, JWTs, `password=` assignments) are stripped
by `core/redact.py` before anything is stored in history, logged, or spoken.
Identity facts live in `data/memory.db` (local SQLite) and never leave the
machine. The RAM answer comes from the `vitals` tool's top-RAM process
breakdown (`spy_vitals.top_ram`).
