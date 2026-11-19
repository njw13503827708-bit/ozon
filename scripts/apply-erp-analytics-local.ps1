$ErrorActionPreference = 'Stop'
$devRoot = Split-Path $PSScriptRoot -Parent
$liveRoot = 'D:\下载文件'
$backupRoot = Join-Path (Split-Path $devRoot -Parent) 'erp-repair-20261002/analytics-before'
$service = Get-CimInstance Win32_Process -Filter 'ProcessId=38448'
$listener = Get-NetTCPConnection -LocalPort 8798 -State Listen
if ($listener.OwningProcess -ne 38448 -or $service.CommandLine -notlike '*uvicorn app:app*--port 8798*') { throw 'ERP process changed; update stopped' }
$files = @('backend/api/stores.py','backend/db/erp.py','backend/repositories/erp.py','backend/services/store_service.py','collector/local-ingest/app.py','backend/api/analytics.py','backend/integrations/ozon/analytics_client.py','backend/repositories/analytics.py','backend/services/analytics_service.py','migrations/013_listing_platform_skus.sql','migrations/014_analytics_snapshots.sql','collector/workbench-command-center/dist/index.html')
New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
$env:ERP_UPDATE_LIVE = $liveRoot
$env:ERP_UPDATE_BACKUP = $backupRoot
@'
import os, sqlite3
from pathlib import Path
live=Path(os.environ['ERP_UPDATE_LIVE']); backup=Path(os.environ['ERP_UPDATE_BACKUP'])
with sqlite3.connect(live/'runtime/erp.sqlite3') as source:
    if source.execute("SELECT COUNT(*) FROM sync_jobs WHERE status IN ('QUEUED','RUNNING','RETRYING')").fetchone()[0]:
        raise SystemExit('Active synchronization; stop update')
    with sqlite3.connect(backup/'erp.sqlite3') as target:
        source.backup(target)
'@ | & (Join-Path $devRoot '.venv/Scripts/python.exe') -
if ($LASTEXITCODE -ne 0) { throw 'Database preflight failed' }
foreach ($file in $files) {
    $livePath = Join-Path $liveRoot $file
    if (Test-Path -LiteralPath $livePath) {
        $backupPath = Join-Path $backupRoot $file
        New-Item -ItemType Directory -Path (Split-Path $backupPath -Parent) -Force | Out-Null
        Copy-Item -LiteralPath $livePath -Destination $backupPath
    }
}
Stop-Process -Id 38448
foreach ($file in $files) {
    $livePath = Join-Path $liveRoot $file
    New-Item -ItemType Directory -Path (Split-Path $livePath -Parent) -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $devRoot $file) -Destination $livePath -Force
}
Get-ChildItem -LiteralPath (Join-Path $devRoot 'collector/workbench-command-center/dist/assets') -File | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $liveRoot 'collector/workbench-command-center/dist/assets') -Force
}
Start-Process -FilePath (Join-Path $liveRoot '.venv/Scripts/python.exe') -ArgumentList @('-m','uvicorn','app:app','--app-dir','collector/local-ingest','--host','127.0.0.1','--port','8798') -WorkingDirectory $liveRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $backupRoot 'service.out.log') -RedirectStandardError (Join-Path $backupRoot 'service.err.log')
Write-Output 'ERP source and frontend updated; backup preserved. Verify localhost before claiming success.'
