param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
$listener = Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if (-not $listener) { Write-Output 'LoveShe is not running.'; exit 0 }
foreach ($listenerPid in @($listener.OwningProcess | Select-Object -Unique)) {
    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $listenerPid"
    if ($processInfo.Name -notmatch '^python(w)?\.exe$' -or $processInfo.CommandLine -notmatch "-m\s+loveshe\.server\s+--port\s+$Port(?:\s|$)") {
        throw 'The port is owned by another application; it has not been stopped.'
    }
    $url = "http://127.0.0.1:$Port"
    $page = Invoke-WebRequest -Uri $url -UseBasicParsing
    $tokenMatch = [regex]::Match($page.Content, 'name="loveshe-token" content="([a-f0-9]+)"')
    if (-not $tokenMatch.Success) { throw 'Cannot verify LoveShe status.' }
    $appState = Invoke-RestMethod -Uri "$url/api/state" -Headers @{'X-LoveShe-Token'=$tokenMatch.Groups[1].Value}
    if (@($appState.jobs | Where-Object { $_.state -in @('queued','running') }).Count -gt 0) {
        throw 'A task is still running. Wait for it to finish, then stop LoveShe.'
    }
    Stop-Process -Id $listenerPid
}
Write-Output 'LoveShe stopped. Saved data remains on this computer.'
