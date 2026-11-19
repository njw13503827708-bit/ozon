from __future__ import annotations
from backend.db.erp import initialize as initialize_erp_test_database

import tempfile
import unittest
from pathlib import Path

from backend.db.erp import database
from backend.repositories.erp import ErpRepository
from tests.test_legacy_product_index import artifact_hashes, load_indexer, make_known_product


class LegacyProductIndexIdempotencyTest(unittest.TestCase):
    def test_second_index_keeps_ids_counts_and_source_files_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            products_root = root / "source-products"
            erp_root = root / "erp-runtime"
            make_known_product(products_root)
            initialize_erp_test_database(erp_root)
            repo = ErpRepository(erp_root)
            repo.upsert_store({"id": "store-a", "platform": "OZON"})
            before = artifact_hashes(products_root)

            first_report = load_indexer()(products_root, erp_root)
            with database(erp_root) as db:
                first = {
                    table: [tuple(row) for row in db.execute(f"SELECT id FROM {table} ORDER BY id")]
                    for table in ("products", "skus", "marketplace_listings")
                }
            second_report = load_indexer()(products_root, erp_root)
            with database(erp_root) as db:
                second = {
                    table: [tuple(row) for row in db.execute(f"SELECT id FROM {table} ORDER BY id")]
                    for table in ("products", "skus", "marketplace_listings")
                }

            self.assertEqual(first, second)
            self.assertEqual({"products": 1, "skus": 1, "listings": 1}, first_report["indexed"])
            self.assertEqual(first_report["indexed"], second_report["indexed"])
            self.assertEqual(before, artifact_hashes(products_root))


if __name__ == "__main__":
    unittest.main()
