@echo off
cd /d C:\Users\Svelt\SKY
tasklist /FI "IMAGENAME eq python.exe" /FO CSV 2>nul | findstr /I "python.exe" >nul
start "" /min .venv\Scripts\python.exe hub\server.py
timeout /t 2 >nul
start "" http://127.0.0.1:20130
