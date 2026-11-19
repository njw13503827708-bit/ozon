from __future__ import annotations
from backend.db.erp import initialize as initialize_erp_test_database

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from backend.repositories.erp import ErpRepository
from scripts.workbench_stores import mark_store_validation_failed, upsert_store


class StoreCredentialsSecurityTest(unittest.TestCase):
    def test_legacy_scripts_still_import_from_script_entrypoints(self):
        root = Path(__file__).resolve().parents[1]
        environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
        for script in ("scripts/workbench_stores.py", "scripts/task_database.py"):
            result = subprocess.run(
                [sys.executable, script, "--help"], cwd=root, env=environment,
                capture_output=True, text=True,
            )
            self.assertEqual(0, result.returncode, f"{script}: {result.stderr}")

    def test_store_response_registry_and_sqlite_never_contain_secret(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "ozon-adapter").mkdir()
            public = upsert_store(root, {
                "id": "shop-a", "display_name": "Shop A",
                "client_id": "client-secret", "api_key": "api-secret",
            })
            initialize_erp_test_database(root)
            repo = ErpRepository(root)
            repo.upsert_store({
                "id": "shop-a", "credential_ref": "ozon-adapter/.env.shop-a",
                "connection_status": "NEEDS_VALIDATION",
            })

            self.assertNotIn("client-secret", json.dumps(public, ensure_ascii=False))
            self.assertNotIn("api-secret", json.dumps(public, ensure_ascii=False))
            self.assertNotIn("client-secret", (root / "ozon-adapter/shops.json").read_text(encoding="utf-8"))
            self.assertNotIn("api-secret", (root / "ozon-adapter/shops.json").read_text(encoding="utf-8"))
            database = (root / "runtime/erp.sqlite3").read_bytes()
            self.assertNotIn(b"client-secret", database)
            self.assertNotIn(b"api-secret", database)

    def test_upload_credential_failure_cannot_persist_or_return_raw_secrets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            upsert_store(root, {
                "id": "shop-a", "display_name": "Shop A",
                "client_id": "client-secret", "api_key": "api-secret",
            })
            result = mark_store_validation_failed(
                root, "shop-a", "Api-Key api-secret and Client-Id client-secret deactivated",
            )
            visible = json.dumps(result, ensure_ascii=False)
            registry = (root / "ozon-adapter/shops.json").read_text(encoding="utf-8")
            self.assertIn("deactivated", result["last_validation_error"])
            for secret in ("api-secret", "client-secret"):
                self.assertNotIn(secret, visible)
                self.assertNotIn(secret, registry)


if __name__ == "__main__":
    unittest.main()
