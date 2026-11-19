@echo off
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo Missing Python environment. Run scripts\install-candidate.cmd first.
  exit /b 1
)
".venv\Scripts\python.exe" "scripts\launch_candidate.py" --port 8911 --mode READ_ONLY
exit /b %errorlevel%
