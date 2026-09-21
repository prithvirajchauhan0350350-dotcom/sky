$ErrorActionPreference = "Continue"
# 1) kill ALL hub server + extra sky_roam instances (fresh single-instance state)
$procs = Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'"
$roamSeen = 0
foreach ($p in $procs) {
  $cl = $p.CommandLine
  if ($cl -match 'hub\\server\.py') {
    Write-Output ("KILL HUB " + $p.ProcessId)
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
  } elseif ($cl -match 'sky_roam\.py') {
    $roamSeen++
    if ($roamSeen -gt 1) {
      Write-Output ("KILL ROAM-DUP " + $p.ProcessId)
      Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
  }
}
Start-Sleep -Seconds 2
# 2) start exactly one hub (hidden), wait for health
Start-Process -WindowStyle Hidden 'C:\Users\Svelt\SKY\.venv\Scripts\pythonw.exe' -ArgumentList 'hub\server.py' -WorkingDirectory 'C:\Users\Svelt\SKY'
$ok = $false
foreach ($i in 1..40) {
  try {
    $r = Invoke-WebRequest -Uri 'http://127.0.0.1:20130/api/health' -UseBasicParsing -TimeoutSec 2
    if ($r.StatusCode -eq 200) { $ok = $true; break }
  } catch { Start-Sleep -Milliseconds 500 }
}
Write-Output ($(if ($ok) { "HUB_UP" } else { "HUB_DOWN" }))
# 3) verify core routes + NEW agency endpoints
foreach ($u in @('http://127.0.0.1:20130/iron','http://127.0.0.1:20130/iron.css','http://127.0.0.1:20130/iron.js','http://127.0.0.1:20130/api/health','http://127.0.0.1:20130/api/agency')) {
  try {
    $r = Invoke-WebRequest -Uri $u -UseBasicParsing -TimeoutSec 8
    Write-Output ($r.StatusCode + " " + $r.RawContentLength + "B  " + $u)
  } catch { Write-Output ("ERR " + $u + " :: " + $_.Exception.Message) }
}
# 4) agency roster summary
try {
  $a = Invoke-RestMethod -Uri 'http://127.0.0.1:20130/api/agency' -TimeoutSec 10
  Write-Output ("AGENCY total=" + $a.total + " divisions=" + $a.divisions.Count + " active=" + $(if ($a.active) { $a.active.slug } else { "none" }))
} catch { Write-Output ("AGENCY ERR " + $_.Exception.Message) }
