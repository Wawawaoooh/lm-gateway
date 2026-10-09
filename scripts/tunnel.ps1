$ErrorActionPreference = 'Continue'
$exe    = 'F:\cloudflare\cloudflared-windows-amd64.exe'
$cfgP   = 'F:\Peterpertrilli\config.json'
$tries  = 8
$delayS = 10

if (-not (Test-Path $exe)) { Write-Host "cloudflared not found: $exe" ; exit 1 }

$port = 8791
if (Test-Path $cfgP) {
    try { $j = Get-Content $cfgP -Raw | ConvertFrom-Json; if ($j.port) { $port = $j.port } } catch {}
}

Write-Host ("tunnel target : http://localhost:" + $port)
Write-Host 'when a line https://xxxx.trycloudflare.com appears -> copy that URL into Qoder'
Write-Host 'KEEP THIS WINDOW OPEN while the remote PC uses the gateway'

for ($i = 1; $i -le $tries; $i++) {
    Write-Host ("attempt " + $i + " of " + $tries)
    & $exe tunnel --url ('http://localhost:' + $port)
    if ($LASTEXITCODE -eq 0) { break }
    Write-Host ("exit code " + $LASTEXITCODE + " -> retrying in " + $delayS + "s (network/DNS hiccup)")
    Start-Sleep -Seconds $delayS
}
