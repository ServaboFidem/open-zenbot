# Start the harness, wait for it to answer, then open the Electron app.
# The harness is stopped again when the app window closes.
#
#   .\scripts\dev.ps1            # rebuild the GUI, then run
#   .\scripts\dev.ps1 -Build     # kept for compatibility; build always runs
#   .\scripts\dev.ps1 -Port 9000 # harness on another port (ZEN_HARNESS_URL follows)
#
# A key in OPENROUTER_API_KEY binds the provider at start; without one the
# harness starts unbound and the app's Settings page binds it.

param(
    [switch]$Build,
    [int]$Port = 8765,
    [int]$WaitSeconds = 60
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host '== building the GUI'
npm run build
if ($LASTEXITCODE -ne 0) { throw "npm run build failed ($LASTEXITCODE)" }

$env:ZEN_PORT = "$Port"
$env:ZEN_HARNESS_URL = "http://127.0.0.1:$Port"

Write-Host "== starting the harness on $($env:ZEN_HARNESS_URL)"
$harness = Start-Process -FilePath 'uv' -ArgumentList 'run', 'python', '-m', 'src.harness.main' `
    -WorkingDirectory $root -NoNewWindow -PassThru

try {
    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    $ready = $false
    while ((Get-Date) -lt $deadline) {
        if ($harness.HasExited) { throw "the harness exited early (code $($harness.ExitCode))" }
        try {
            $r = Invoke-WebRequest -Uri "$($env:ZEN_HARNESS_URL)/health" -UseBasicParsing -TimeoutSec 2
            if ($r.StatusCode -eq 200) { $ready = $true; break }
        } catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $ready) { throw "the harness did not answer /health within $WaitSeconds s" }
    Write-Host "== harness ready: $($r.Content)"

    Write-Host '== opening the app (close the window to stop everything)'
    npm start
}
finally {
    if (-not $harness.HasExited) {
        Write-Host '== stopping the harness'
        # uv spawns python as a child; take the whole tree down
        & taskkill /PID $harness.Id /T /F | Out-Null
    }
}
