$ErrorActionPreference = "Continue"
$B = "http://127.0.0.1:20130"
# 1) HUD page serves with agency markup
$p = Invoke-WebRequest -Uri "$B/iron" -UseBasicParsing -TimeoutSec 8
Write-Output ("IRON " + $p.StatusCode + " len=" + $p.RawContentLength + " hasAgencyBtn=" + $p.Content.Contains("agency-btn"))
# 2) detail fetch
$d = Invoke-RestMethod -Uri "$B/api/agency/agent?slug=engineering-frontend-developer" -TimeoutSec 10
Write-Output ("DETAIL " + $d.meta.name + " | body_chars=" + $d.body.Length)
# 3) activate
$a = Invoke-RestMethod -Method Post -Uri "$B/api/agency/activate" -Body '{"slug":"engineering-frontend-developer"}' -ContentType "application/json" -TimeoutSec 10
Write-Output ("ACTIVATE ok=" + $a.ok + " -> " + $a.active.name)
# 4) one real brain turn with the specialist active (full agent_turn path)
$c = Invoke-RestMethod -Method Post -Uri "$B/api/chat" -Body '{"text":"In one short line: which specialist mode is running right now?"}' -ContentType "application/json" -TimeoutSec 120
Write-Output ("CHAT " + ($c.reply -replace "\s+", " ").Substring(0, [Math]::Min(220, $c.reply.Length)))
# 5) roster reflects active + stand down
$r = Invoke-RestMethod -Uri "$B/api/agency" -TimeoutSec 10
Write-Output ("ACTIVE-NOW " + $(if ($r.active) { $r.active.slug } else { "none" }))
$x = Invoke-RestMethod -Method Post -Uri "$B/api/agency/deactivate" -TimeoutSec 10
Write-Output ("DEACT ok=" + $x.ok)
$r2 = Invoke-RestMethod -Uri "$B/api/agency" -TimeoutSec 10
Write-Output ("ACTIVE-AFTER " + $(if ($r2.active) { $r2.active.slug } else { "none" }))
