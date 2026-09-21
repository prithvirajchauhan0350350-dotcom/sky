@echo off
setlocal EnableExtensions
title SKY Installer
echo ============================================================
echo    SKY  -  Personal AI Assistant  -  Installer
echo    (Windows 10/11, kisi bhi naye PC pe chalega)
echo ============================================================
echo.

pushd "%~dp0"
set "SKYDIR=%CD%"

rem ---- 1. find python ----
set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY python --version >nul 2>nul && set "PY=python"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY if exist "C:\Program Files\Python312\python.exe" set "PY=C:\Program Files\Python312\python.exe"
if not defined PY (
    echo Python 3 nahi mila. winget se install kar raha hoon...
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    if not defined PY if exist "C:\Program Files\Python312\python.exe" set "PY=C:\Program Files\Python312\python.exe"
)
if not defined PY (
    echo.
    echo [X] Python install nahi ho paya. Python 3.12 install karke dobara chalao.
    pause
    exit /b 1
)
echo [1/6] Python mila:
%PY% --version

echo.
echo [2/6] Virtual environment bana raha hoon (.venv)...
if not exist .venv %PY% -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip -q

echo [3/6] Core packages install ho rahe hain (2-5 min lagenge)...
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
    echo [X] pip install fail hua. Internet check karke dobara chalao.
    pause
    exit /b 1
)

echo [4/6] Addon environment bana raha hoon (browser automation)...
if not exist addons-env %PY% -m venv addons-env
addons-env\Scripts\python.exe -m pip install -q -U browser-use python-dotenv

echo [5/6] Autostart (boot pe on) + watchdog (har 5 min crash-restart)...
if not exist logs mkdir logs
if not exist data mkdir data
if not exist spy_screenshots mkdir spy_screenshots
if not exist .env copy /y .env.example .env >nul
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v SKY /t REG_SZ /d "\"%SKYDIR%\.venv\Scripts\python.exe\" \"%SKYDIR%\skyd.py\"" /f >nul
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v SKYEAR /t REG_SZ /d "\"%SKYDIR%\.venv\Scripts\python.exe\" \"%SKYDIR%\skyear.py\"" /f >nul
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v SKYHUB /t REG_SZ /d "\"%SKYDIR%\.venv\Scripts\pythonw.exe\" \"%SKYDIR%\hub\server.py\"" /f >nul
schtasks /Create /F /TN "SKY Watchdog" /SC MINUTE /MO 5 /TR "\"%SKYDIR%\.venv\Scripts\pythonw.exe\" \"%SKYDIR%\watchdog.py\"" >nul

echo [6/6] Sky start kar raha hoon...
start "" /min "%SKYDIR%\.venv\Scripts\python.exe" "%SKYDIR%\skyd.py"
start "" /min "%SKYDIR%\.venv\Scripts\python.exe" "%SKYDIR%\skyear.py"
start "" "%SKYDIR%\.venv\Scripts\pythonw.exe" "%SKYDIR%\hub\server.py"
timeout /t 3 >nul
start "" http://127.0.0.1:20130

echo.
echo ============================================================
echo   SKY INSTALL HO GAYA!
echo   - Boot pe apne aap start: skyd + skyear + hub (SKY/SKYEAR/SKYHUB)
echo   - Har 5 min watchdog: crash hui service wapas start
echo   - Web UI:  http://127.0.0.1:20130
echo   - Voice:   "hey sky" bolke jagao (skyear chahiye)
echo.
echo   EK AAKHRI KAAM:
echo   1. .env me apni API key daalo (file abhi khul rahi hai)
echo   2. config.json kholo, "brain" profile me set karo:
echo        base_url = apna OpenAI-compatible endpoint
echo        model    = apna model (jaise glm-5.3-flash)
echo ============================================================
notepad .env
popd
