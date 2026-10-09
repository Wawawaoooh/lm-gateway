$ErrorActionPreference = 'Continue'
Set-Location F:\Peterpertrilli
Remove-Item F:\cloudflare\cf2.log, F:\cloudflare\gw.log -ErrorAction SilentlyContinue

cmd /c start "" /min cmd /c ".venv\Scripts\python.exe scripts\headless_server.py > F:\cloudflare\gw.log 2>&1"
Start-Sleep -Seconds 5
Write-Host '--- gateway local healthz:'
(Invoke-RestMethod http://127.0.0.1:8791/healthz) | ConvertTo-Json

cmd /c start "" /min cmd /c "F:\cloudflare\cloudflared-windows-amd64.exe tunnel --url http://localhost:8791 > F:\cloudflare\cf2.log 2>&1"
Start-Sleep -Seconds 30
$m = Select-String -Path F:\cloudflare\cf2.log -Pattern 'https://[a-z0-9.-]+\.trycloudflare\.com' | Select-Object -Last 1
if (-not $m) { Write-Host 'NO URL in log'; Get-Content F:\cloudflare\cf2.log | Select-Object -Last 8; exit }
$u = $m.Matches[0].Value
Write-Host "TUNNEL=$u"

for ($i = 0; $i -lt 4; $i++) {
    try { Invoke-RestMethod ($u + '/healthz') -TimeoutSec 30 | ConvertTo-Json; break }
    catch { Write-Host ("public try " + $i + ' : ' + $_.Exception.Message); Start-Sleep -Seconds 8 }
}

try { Invoke-RestMethod ($u + '/v1/models') | Out-Null; Write-Host 'UNAUTH UNEXPECTEDLY PASSED' }
catch { Write-Host ('unauth status=' + [int]$_.Exception.Response.StatusCode) }

$t = & F:\Peterpertrilli\.venv\Scripts\python.exe -c "from gateway.keystore import KeyStore; ks=KeyStore(); info,tk=ks.create('e2e-check'); print(tk)"
Write-Host 'temp key created'
try { (Invoke-RestMethod ($u + '/v1/models') -Headers @{ Authorization = 'Bearer ' + $t }).data | Select-Object -First 3 id } catch { Write-Host ('authed fail: ' + $_.Exception.Message) }

& F:\Peterpertrilli\.venv\Scripts\python.exe -c "from gateway.keystore import KeyStore; ks=KeyStore(); ks.revoke(name='e2e-check'); print('key revoked')"
taskkill /IM cloudflared-windows-amd64.exe /F | Out-Null
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'headless_server' } | ForEach-Object { Stop-Process -Id $_.ProcessID -Force }
Write-Host '--- cleanup done'
