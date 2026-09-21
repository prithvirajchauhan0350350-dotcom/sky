SKY — PERSONAL AI ASSISTANT (Windows)
=====================================

Kya hai:
- Sky hamesha system pe chalti hai: reminders, daily briefing, wake word
  ("hey sky"), web UI, browser automation, long-term memory.
- Boot pe khud start hoti hai; har 5 min ek watchdog crash hui service
  wapas start kar deta hai.

INSTALL (naya PC):
1. Ye folder kahin bhi extract karo (jaise C:\Sky).
2. INSTALL-SKY.bat pe double-click karo.
   - Python nahi hai to khud install ho jayega (winget).
   - Core packages + browser-automation addon apne aap lagenge.
   - Autostart + watchdog set ho jayenge; Sky turant start ho jayegi.
3. Install ke last me .env file khulegi:
   - OMNIROUTE_API_KEY= me apni API key daalo, save karo.
4. config.json kholo, "brain" section me apna endpoint set karo:
   - profiles.<active>.base_url = apna OpenAI-compatible URL
   - profiles.<active>.model    = apna model
5. Browser: http://127.0.0.1:20130  → Sky Hub (chat, vitals, files, spy).

ROZANA USE:
- Web UI:    http://127.0.0.1:20130 (boot pe khud chalu)
- Voice:     "hey sky" bolo (skyear window background me chalti hai)
- Reminder:  Hub chat me bolo "9 baje paani peene ka reminder lagao"

KYA-KYA ANDAR HAI:
- skyd.py        daemon: reminders + briefing + background learning
- skyear.py      wake-word listener
- hub/server.py  web UI (port 20130)
- watchdog.py    har 5 min sab zinda rakhta hai
- core/          agent brain (38 tools: memory, files, sim, browser, skills)
- addons-env/    browser automation (browser-use) ka alag venv

NOTES:
- Ek PC pe ek hi install karo.
- API key kabhi kisi ko mat dena (.env file me hai).
- Hatana ho to UNINSTALL-SKY.bat chalao (files folder me reh jayengi).
- Skill libraries (repos/) core install me nahi aati — chahiye to alag
  se copy karo; unke bina Sky sab kuch normal chalati hai.

TECHNICAL:
- Install: INSTALL-SKY.bat  (venv + pip + reg Run entries + scheduled task)
- Autostart entries: HKCU\...\Run  ->  SKY, SKYEAR, SKYHUB
- Watchdog task:    Task Scheduler -> "SKY Watchdog" (har 5 min)
- Uninstall: UNINSTALL-SKY.bat
