import unittest
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WindowsSetupTest(unittest.TestCase):
    def test_bootstrap_has_utf8_bom_for_windows_powershell_51(self):
        self.assertTrue((ROOT / "scripts/setup_windows.ps1").read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_codex_facing_windows_bootstrap_builds_both_frontends(self):
        script = (ROOT / "scripts/setup_windows.ps1").read_text(encoding="utf-8")
        self.assertIn("Python.Python.3.12", script)
        self.assertIn("OpenJS.NodeJS.LTS", script)
        self.assertIn("@openai/codex", script)
        self.assertIn('Push-Location "collector\\workbench-command-center"', script)
        self.assertIn('Push-Location "collector\\edge-extension"', script)
        self.assertIn("npm.cmd run build", script)
        self.assertIn(".venv\\Scripts\\python.exe", script)
        self.assertIn("shops.example.json", script)
        self.assertIn("-m uvicorn", script)
        self.assertIn("winget install --id $PackageId --exact --force", script)
        self.assertLess(script.index("Refresh-Path\nInstall-PackageIfMissing"), script.index('Install-PackageIfMissing "git"'))

    def test_bootstrap_rebuilds_an_existing_but_unrunnable_venv(self):
        script = (ROOT / "scripts/setup_windows.ps1").read_text(encoding="utf-8")
        self.assertIn("Test-CommandAvailable $VenvPython", script)
        self.assertIn("python -m venv --clear .venv", script)

    def test_pipeline_uses_platform_specific_venv_and_process_groups(self):
        runner = (ROOT / "scripts/run_batch.py").read_text(encoding="utf-8")
        self.assertIn('"Scripts/python.exe" if os.name == "nt" else "bin/python"', runner)
        self.assertIn("subprocess.CREATE_NEW_PROCESS_GROUP", runner)
        self.assertIn('["taskkill", "/PID", str(process.pid), "/T", "/F"]', runner)

    def test_readme_warns_that_github_source_requires_frontend_build(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("setup_windows.ps1", readme)
        self.assertIn("dist", readme)

    def test_project_self_check_runs_from_windows_documented_entrypoint(self):
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts/project_self_check.py")],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        self.assertNotIn("ModuleNotFoundError", completed.stderr)
        self.assertIn(json.loads(completed.stdout.strip().splitlines()[-1])["decision"], {"PASS", "FAIL"})


if __name__ == "__main__":
    unittest.main()
