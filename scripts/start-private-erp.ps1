$ErrorActionPreference = 'Stop'
$erpRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $erpRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Missing .venv. Follow the cloud migration guide before starting ERP.' }
if (-not (Test-Path -LiteralPath (Join-Path $erpRoot 'collector/workbench-command-center/dist/index.html'))) { throw 'Missing frontend build. Use the complete source package.' }
$health = $null
try { $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8798/health' -TimeoutSec 3 } catch { }
if ($health) {
    if ($health.service -ne 'collector-local-ingest' -or $health.root -ne $erpRoot) { throw 'Port 8798 belongs to another project. No process was stopped.' }
    Write-Output 'ERP is already running: http://127.0.0.1:8798/command-center'
    exit 0
}
Set-Location -LiteralPath $erpRoot
$logRoot = Join-Path $erpRoot 'logs'
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$process = Start-Process -FilePath $python -ArgumentList @('-m','uvicorn','app:app','--app-dir','collector/local-ingest','--host','127.0.0.1','--port','8798','--lifespan','off') -WorkingDirectory $erpRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logRoot "erp-$stamp.out.log") -RedirectStandardError (Join-Path $logRoot "erp-$stamp.err.log") -PassThru
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    Start-Sleep -Milliseconds 500
    try { $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8798/health' -TimeoutSec 2 } catch { continue }
    if ($health.service -ne 'collector-local-ingest' -or $health.root -ne $erpRoot) { throw 'Unexpected service identity; inspect the startup logs.' }
    Write-Output "ERP started independently (launcher PID $($process.Id)): http://127.0.0.1:8798/command-center"
    Write-Output 'Startup recovery and unattended schedulers are disabled for this local acceptance run. No autostart was installed.'
    exit 0
}
throw "ERP did not become ready; inspect logs/erp-$stamp.err.log"
