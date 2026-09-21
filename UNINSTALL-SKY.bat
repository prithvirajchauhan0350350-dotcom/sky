@echo off
title SKY Uninstaller
pushd "%~dp0"
echo Sky ko band karke autostart hata raha hoon...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" | Where-Object { $_.CommandLine -match 'skyd\.py|skyear\.py|hub\\server\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | Where-Object { $_.CommandLine -match 'dist\\cli\.mjs' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
taskkill /IM iii.exe /F >nul 2>nul
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v SKY /f >nul 2>nul
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v SKYEAR /f >nul 2>nul
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v SKYHUB /f >nul 2>nul
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v SKYAMEM /f >nul 2>nul
schtasks /Delete /TN "SKY Watchdog" /F >nul 2>nul
echo.
echo Done. Sky band hai, boot autostart aur watchdog hat gaye.
echo Files %CD% me pade hain — folder delete karke saaf.
pause
