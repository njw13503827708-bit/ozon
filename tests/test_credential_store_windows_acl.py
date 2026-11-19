from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from backend.integrations.ozon.credential_store import CredentialStore
from scripts.task_database import initialize as initialize_task_database
from tests.test_credential_store import SHOP


def windows_acl(path: Path) -> dict:
    result = subprocess.run(
        ["icacls", str(path)],
        check=True, capture_output=True,
    )
    account = subprocess.run(
        ["whoami"], check=True, capture_output=True,
    ).stdout.decode('oem').strip()
    assert isinstance(result.stdout,bytes), 'icacls did not produce captured native output'
    lines = result.stdout.decode('oem').splitlines()
    assert lines[0].startswith(str(path)), result.stdout
    entries = [lines[0][len(str(path)):].strip(), *[line.strip() for line in lines[1:] if line.startswith(" ")]]
    return {"current_account": account, "entries": [entry for entry in entries if entry]}


@unittest.skipUnless(os.name == "nt", "Windows ACL verification requires Windows")
class CredentialStoreWindowsAclTest(unittest.TestCase):
    def test_windows_write_removes_inheritance_and_grants_current_user_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            calls = []
            store = CredentialStore(
                Path(temporary), platform="nt", principal="S-1-5-21-test",
                acl_runner=lambda args: calls.append(args),
            )
            store.write(SHOP, "client-1", "secret-1")
            self.assertEqual("icacls", calls[0][0].lower())
            self.assertIn("/inheritance:r", calls[0])
            self.assertIn("/grant:r", calls[0])
            self.assertNotIn("secret-1", " ".join(calls[0]))

    def test_real_secret_file_acl_is_owner_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            CredentialStore(root).write(SHOP, "client-1", "secret-1")
            acl = windows_acl(root / "ozon-adapter/.env.shop-a")
            entries = acl["entries"]
            self.assertTrue(entries)
            self.assertEqual(1, len(entries))
            self.assertEqual(f"{acl['current_account']}:(F)".casefold(), entries[0].casefold())

    def test_real_task_database_acl_is_owner_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = initialize_task_database(root)
            acl = windows_acl(path)
            entries = acl["entries"]
            self.assertEqual(1, len(entries))
            self.assertEqual(f"{acl['current_account']}:(F)".casefold(), entries[0].casefold())


if __name__ == "__main__":
    unittest.main()
