# =============================================================================
#  run.ps1 - bring up the QuoteFlow Supplier Outreach Desk for an interactive demo
#
#  Runs uvicorn in the FOREGROUND so you watch the tagged pipeline log
#  ([PIPE] [LLM ] [MATCH] [VDB ] ...) scroll live while you drive the browser UI
#  at http://127.0.0.1:8000/  (Ctrl+C to stop).
#
#  Config comes from .env in the repo root (LLM provider, poller, step delay,
#  colours). This script only frees the port, optionally reseeds, and launches.
#
#  Usage:
#     .\run.ps1                 # use .env as-is (Ollama, poller on, 20s step delay)
#     .\run.ps1 -Reseed         # wipe quoteflow.db + chroma_data, reseed --demo, then run
#     .\run.ps1 -Fast           # override .env: stub LLM, no step delay, poller off
#     .\run.ps1 -Reseed -Fast   # fresh + fast
#     .\run.ps1 -Port 8010      # bind a different port
# =============================================================================
[CmdletBinding()]
param(
    [switch] $Reseed,
    [switch] $Fast,
    [int]    $Port = 8000
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Info($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "!!  $m" -ForegroundColor Yellow }

# --- 1. free the port -------------------------------------------------------
$held = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($held) {
    foreach ($procId in ($held.OwningProcess | Select-Object -Unique)) {
        try {
            $p = Get-Process -Id $procId -ErrorAction Stop
            Warn "port $Port held by $($p.ProcessName) (pid $procId) - stopping it"
            Stop-Process -Id $procId -Force
        } catch { }
    }
    Start-Sleep -Seconds 2
}

# --- 2. Ollama reachable? (only matters when .env uses LLM_PROVIDER=ollama) -
if (-not $Fast) {
    try {
        Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -TimeoutSec 3 | Out-Null
        Info "Ollama is up"
    } catch {
        Warn "Ollama not reachable on :11434 - start it with 'ollama serve' (or run .\run.ps1 -Fast for the stub LLM)"
    }
}

# --- 3. optional wipe + reseed -------------------------------------------------
if ($Reseed) {
    Info "wiping quoteflow.db + chroma_data and reseeding with --demo"
    Remove-Item -Force  -ErrorAction SilentlyContinue quoteflow.db, quoteflow.db-wal, quoteflow.db-shm, last_history_id.json
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue chroma_data
    $seedEnv = if ($Fast) { "stub" } else { $null }
    if ($seedEnv) { $env:LLM_PROVIDER = $seedEnv }
    uv run python -m gmail_trigger_poc.seed --demo
    if ($seedEnv) { Remove-Item Env:\LLM_PROVIDER }
}

# --- 4. -Fast overrides (process env beats .env) ----------------------------
if ($Fast) {
    $env:LLM_PROVIDER = "stub"
    $env:PIPELINE_STEP_DELAY_SECONDS = "0"
    $env:ENABLE_POLLER = "false"
    Info "FAST mode: stub LLM, no step delay, poller off"
}

# --- 5. run (foreground) ---------------------------------------------------
Info "starting uvicorn on http://127.0.0.1:$Port/   (Ctrl+C to stop)"
Write-Host "    sign in with the prefilled operator@quoteflow.io / demo1234, or 'Continue as demo user'" -ForegroundColor DarkGray
Write-Host ""
uv run uvicorn gmail_trigger_poc.app:app --host 127.0.0.1 --port $Port
