from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.integrations.ozon.credential_store import CredentialStore


SHOP = {
    "id": "shop-a",
    "client_id_env": "OZON_SHOP_A_CLIENT_ID",
    "api_key_env": "OZON_SHOP_A_API_KEY",
}


class CredentialStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_round_trip_keeps_legacy_env_format(self):
        store = CredentialStore(self.root)
        store.write(SHOP, "client-1", "secret-1")

        path = self.root / "ozon-adapter/.env.shop-a"
        self.assertEqual(
            "OZON_SHOP_A_CLIENT_ID=client-1\nOZON_SHOP_A_API_KEY=secret-1\n",
            path.read_text(encoding="utf-8"),
        )
        self.assertEqual("client-1", store.read(SHOP)["OZON_SHOP_A_CLIENT_ID"])
        self.assertEqual("secret-1", store.read(SHOP)["OZON_SHOP_A_API_KEY"])
        self.assertNotIn("secret-1", repr(store))
        store.delete(SHOP)
        self.assertFalse(path.exists())

    def test_rejects_path_traversal_and_multiline_secret(self):
        store = CredentialStore(self.root)
        with self.assertRaises(ValueError):
            store.write({**SHOP, "id": "../outside"}, "client-1", "secret-1")
        with self.assertRaises(ValueError):
            store.write(SHOP, "client-1", "secret-1\nINJECTED=value")
        self.assertFalse((self.root / "ozon-adapter/.env.shop-a").exists())

    def test_acl_failure_never_replaces_existing_secret_or_echoes_it(self):
        store = CredentialStore(self.root)
        store.write(SHOP, "client-1", "old-secret")

        def fail_acl(_args):
            raise OSError("new-secret leaked from runner")

        failing = CredentialStore(
            self.root, platform="nt", acl_runner=fail_acl, principal="S-1-5-21-test"
        )
        with self.assertRaises(OSError) as caught:
            failing.write(SHOP, "client-2", "new-secret")
        self.assertNotIn("new-secret", str(caught.exception))
        self.assertEqual("old-secret", store.read(SHOP)["OZON_SHOP_A_API_KEY"])


if __name__ == "__main__":
    unittest.main()
