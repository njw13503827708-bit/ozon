@echo off
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 exit /b 1
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt -r requirements-collector.txt
if errorlevel 1 exit /b 1
echo Dependencies ready. For NEW data only, run:
echo .venv\Scripts\python.exe scripts\candidate_server.py --initialize-new
echo Existing databases must be backed up and checked before a separate upgrade.
exit /b 0
