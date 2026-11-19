param([switch]$InitializeNewData)
$ErrorActionPreference='Stop'
$candidateRoot=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $candidateRoot
if (-not (Test-Path -LiteralPath 'collector/workbench-command-center/dist/index.html')) { throw '缺少同源前端产物，请使用完整候选包。' }
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
    & py -3 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw '需要先安装 Python 3.12；客户运行不需要Git、Node或Codex。' }
}
$candidatePython=Join-Path $candidateRoot '.venv/Scripts/python.exe'
& $candidatePython -m pip install -r requirements.txt -r requirements-collector.txt
if ($LASTEXITCODE -ne 0) { throw 'Python依赖安装失败，未启动服务。' }
if ($InitializeNewData) {
    & $candidatePython scripts/candidate_server.py --initialize-new
    if ($LASTEXITCODE -ne 0) { throw '新数据初始化停止，已有库不会覆盖。' }
}
Write-Output '安装完成；使用 scripts/start-candidate.ps1。已有库升级先在线备份，禁止重跑新库初始化。'
