$ErrorActionPreference = "Continue"
# 1) kill old hub server (CommandLine match, launcher+child safe)
$procs = Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" |
  Where-Object { $_.CommandLine -match 'hub\\server\.py' }
foreach ($p in $procs) { Write-Output ("KILL " + $p.ProcessId); Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2
# 2) relaunch hidden
Start-Process -WindowStyle Hidden 'C:\Users\Svelt\SKY\.venv\Scripts\pythonw.exe' -ArgumentList 'hub\server.py' -WorkingDirectory 'C:\Users\Svelt\SKY'
# 3) wait for health
$ok = $false
foreach ($i in 1..40) {
  try {
    $r = Invoke-WebRequest -Uri 'http://127.0.0.1:20130/api/health' -UseBasicParsing -TimeoutSec 2
    if ($r.StatusCode -eq 200) { $ok = $true; break }
  } catch { Start-Sleep -Milliseconds 500 }
}
Write-Output ($(if ($ok) { "HUB_UP" } else { "HUB_DOWN" }))
# 4) route checks
foreach ($u in @('http://127.0.0.1:20130/iron','http://127.0.0.1:20130/iron.css','http://127.0.0.1:20130/iron.js','http://127.0.0.1:20130/api/vitals','http://127.0.0.1:20130/api/avatar')) {
  try {
    $r = Invoke-WebRequest -Uri $u -UseBasicParsing -TimeoutSec 8
    Write-Output ($r.StatusCode + " " + $r.Headers['Content-Type'] + " " + $r.RawContentLength + "B  " + $u)
  } catch { Write-Output ("ERR " + $u) }
}
