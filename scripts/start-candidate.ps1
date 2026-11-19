param([ValidateRange(1024,65535)][int]$Port=8911,[ValidateSet('DEMO','READ_ONLY','LIVE')][string]$Mode='READ_ONLY')
$ErrorActionPreference='Stop'
if ($Port -in @(8798,8904,8910)) { throw '禁止使用原服务端口。' }
$candidateRoot=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$candidatePython=Join-Path $candidateRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $candidatePython)) { throw '请先运行install_candidate.ps1。' }
$candidateManifest=Join-Path $candidateRoot 'candidate-manifest.json'
if (-not (Test-Path -LiteralPath $candidateManifest)) { throw '缺少候选版本清单。' }
$expectedBuild=(Get-Content -LiteralPath $candidateManifest -Raw | ConvertFrom-Json).build
$candidateHealth=$null
try { $candidateHealth=Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2 } catch { }
if ($candidateHealth) {
    if ($candidateHealth.root -ne $candidateRoot -or $candidateHealth.mode -ne $Mode -or $candidateHealth.build -ne $expectedBuild) { throw '端口属于其他目录/版本/模式；没有停止进程。' }
    Write-Output "候选已运行：http://127.0.0.1:$Port/command-center";exit 0
}
$candidateLog=Join-Path $candidateRoot 'logs';New-Item -ItemType Directory -Path $candidateLog -Force | Out-Null
$candidateStamp=Get-Date -Format 'yyyyMMdd-HHmmss'
$candidateProcess=Start-Process -FilePath $candidatePython -ArgumentList @('-B','scripts/candidate_server.py','--port',"$Port",'--mode',$Mode) -WorkingDirectory $candidateRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $candidateLog "candidate-$candidateStamp.out.log") -RedirectStandardError (Join-Path $candidateLog "candidate-$candidateStamp.err.log") -PassThru
for ($attempt=0;$attempt -lt 30;$attempt++) {
    Start-Sleep -Milliseconds 500
    try { $candidateHealth=Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2 } catch { continue }
    if ($candidateHealth.root -ne $candidateRoot -or $candidateHealth.mode -ne $Mode -or $candidateHealth.build -ne $expectedBuild) { throw '启动后身份不符，查看日志；未停止任何进程。' }
    Write-Output "独立候选入口：http://127.0.0.1:$Port/command-center · $Mode · $expectedBuild";exit 0
}
throw "候选未就绪，查看logs/candidate-$candidateStamp.err.log；未安装开机任务或系统服务。"
