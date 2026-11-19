from __future__ import annotations
from backend.db.erp import initialize as initialize_erp_test_database

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.db.erp import DEFAULT_ORGANIZATION_ID, database
from backend.repositories.erp import ErpRepository


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def artifact_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(item for item in root.rglob("*") if item.is_file())
    }


def load_indexer():
    spec = importlib.util.find_spec("backend.services.legacy_product_index_service")
    if spec is None:
        raise AssertionError("legacy product index service is not implemented")
    from backend.services.legacy_product_index_service import index_legacy_products

    return index_legacy_products


def make_known_product(products_root: Path) -> None:
    product = products_root / "P000101"
    write_json(product / "input/source.json", {
        "product_id": "P000101",
        "source_kind": "workbench_collection",
        "source_url": "https://detail.1688.com/offer/101.html",
        "title_cn": "圆形透明密封保鲜盒",
        "skus": [{
            "sku_id": "SKU-101",
            "sku_name": "280ml",
            "purchase_price": 8.5,
            "option_values": [{"value_cn": "透明 280ml"}],
        }],
    })
    write_json(product / "output/store-publications.json", {
        "schema_version": "1.0.0",
        "product_id": "P000101",
        "stores": {
            "store-a": {
                "selected": True,
                "status": "SUCCESS",
                "sku_publications": [{
                    "sku_id": "SKU-101",
                    "offer_id": "OFFER-101",
                    "ozon_product_id": "9001",
                    "initial_price_rub": 1299,
                    "moderation_status": "approved",
                }],
            },
            "missing-store": {
                "selected": True,
                "status": "SUCCESS",
                "sku_publications": [{
                    "sku_id": "SKU-101",
                    "offer_id": "OFFER-MISSING-STORE",
                }],
            },
        },
    })
    write_json(product / "output/ignored-artifact.json", {"must_remain": "untouched"})


class LegacyProductIndexTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.products_root = self.root / "source-products"
        self.erp_root = self.root / "erp-runtime"
        make_known_product(self.products_root)
        write_json(self.products_root / "P000999/input/source.json", {
            "product_id": "P000999",
            "skus": "unknown-format",
        })
        initialize_erp_test_database(self.erp_root)
        self.repo = ErpRepository(self.erp_root)
        self.repo.upsert_store({"id": "store-a", "platform": "OZON"})

    def test_indexes_only_known_contracts_without_mutating_source_artifacts(self) -> None:
        before = artifact_hashes(self.products_root)

        report = load_indexer()(self.products_root, self.erp_root)

        self.assertEqual(before, artifact_hashes(self.products_root))
        self.assertEqual({"products": 1, "skus": 1, "listings": 1}, report["indexed"])
        self.assertIn(
            {"product_id": "P000999", "reason": "invalid source.json"},
            report["skipped"],
        )
        self.assertIn(
            {"product_id": "P000101", "store_id": "missing-store", "reason": "unknown store"},
            report["skipped"],
        )

        with database(self.erp_root) as db:
            product = dict(db.execute("SELECT * FROM products").fetchone())
            sku = dict(db.execute("SELECT * FROM skus").fetchone())
            listing = dict(db.execute("SELECT * FROM marketplace_listings").fetchone())
            stores = [row[0] for row in db.execute("SELECT id FROM stores ORDER BY id")]

        self.assertEqual(("LEGACY_PRODUCT:P000101", "P000101", DEFAULT_ORGANIZATION_ID), (
            product["id"], product["source_product_id"], product["organization_id"],
        ))
        self.assertEqual(("LEGACY_SKU:P000101:SKU-101", "LEGACY_PRODUCT:P000101", "SKU-101", DEFAULT_ORGANIZATION_ID), (
            sku["id"], sku["product_id"], sku["seller_sku"], sku["organization_id"],
        ))
        self.assertEqual(("store-a", "LEGACY_PRODUCT:P000101", "LEGACY_SKU:P000101:SKU-101", "OFFER-101", "9001", DEFAULT_ORGANIZATION_ID), (
            listing["store_id"], listing["product_id"], listing["sku_id"], listing["offer_id"],
            listing["ozon_product_id"], listing["organization_id"],
        ))
        self.assertNotEqual(product["id"], sku["id"])
        self.assertNotIn(str(listing["id"]), {product["id"], sku["id"]})
        self.assertEqual(["store-a"], stores)

    def test_reports_unresolvable_skus_and_unknown_offers_instead_of_guessing(self) -> None:
        publication_path = self.products_root / "P000101/output/store-publications.json"
        publication = json.loads(publication_path.read_text(encoding="utf-8"))
        publication["stores"] = {
            "store-a": {
                "selected": True,
                "status": "SUCCESS",
                "sku_publications": [
                    {"sku_id": "NOT-IN-SOURCE", "offer_id": "OFFER-UNKNOWN-SKU"},
                    {"sku_id": "SKU-101", "offer_id": "unknown"},
                ],
            }
        }
        write_json(publication_path, publication)

        report = load_indexer()(self.products_root, self.erp_root)

        self.assertEqual(0, report["indexed"]["listings"])
        reasons = {item["reason"] for item in report["skipped"]}
        self.assertIn("unknown source sku", reasons)
        self.assertIn("missing offer_id", reasons)
        with database(self.erp_root) as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM marketplace_listings").fetchone()[0])

    def test_structured_identity_values_are_invalid_instead_of_stringified(self) -> None:
        write_json(self.products_root / "P000202/input/source.json", {
            "product_id": "P000202",
            "skus": [{"sku_id": {"unexpected": "shape"}}],
        })

        report = load_indexer()(self.products_root, self.erp_root)

        self.assertIn(
            {"product_id": "P000202", "reason": "invalid source.json"},
            report["skipped"],
        )
        with database(self.erp_root) as db:
            self.assertIsNone(db.execute(
                "SELECT id FROM products WHERE source_product_id='P000202'"
            ).fetchone())

    def test_existing_synced_listing_is_not_overwritten_by_legacy_offer(self) -> None:
        self.repo.upsert_product({"id": "OZON:store-a:9001", "title_cn": "已同步商品"})
        self.repo.upsert_listing({
            "store_id": "store-a", "product_id": "OZON:store-a:9001",
            "offer_id": "OFFER-101", "ozon_product_id": "9001",
            "listing_status": "VISIBLE", "visibility": "VISIBLE", "is_fbo_visible": True,
        })
        with database(self.erp_root) as db:
            before = tuple(db.execute(
                "SELECT product_id, sku_id, listing_status, visibility, is_fbo_visible FROM marketplace_listings WHERE store_id='store-a' AND offer_id='OFFER-101'"
            ).fetchone())

        report = load_indexer()(self.products_root, self.erp_root)

        with database(self.erp_root) as db:
            after = tuple(db.execute(
                "SELECT product_id, sku_id, listing_status, visibility, is_fbo_visible FROM marketplace_listings WHERE store_id='store-a' AND offer_id='OFFER-101'"
            ).fetchone())
        self.assertEqual(before, after)
        self.assertEqual(0, report["indexed"]["listings"])
        self.assertIn("existing listing conflict", {row["reason"] for row in report["skipped"]})

    def test_rejects_erp_root_inside_source_tree_before_creating_database(self) -> None:
        before = artifact_hashes(self.products_root)
        with self.assertRaises(ValueError):
            load_indexer()(self.products_root, self.products_root / "runtime")
        self.assertEqual(before, artifact_hashes(self.products_root))

    def test_unsupported_source_version_and_structured_fields_are_reported(self) -> None:
        write_json(self.products_root / "P000202/input/source.json", {
            "schema_version": "99.0.0", "product_id": "P000202",
            "skus": [{"sku_id": "SKU-202"}],
        })
        write_json(self.products_root / "P000203/input/source.json", {
            "schema_version": "1.0.0", "product_id": "P000203",
            "title_cn": {"unexpected": "object"}, "skus": [{"sku_id": "SKU-203"}],
        })
        before = artifact_hashes(self.products_root)

        report = load_indexer()(self.products_root, self.erp_root)

        self.assertEqual(before, artifact_hashes(self.products_root))
        self.assertIn({"product_id": "P000202", "reason": "invalid source.json"}, report["skipped"])
        self.assertIn({"product_id": "P000203", "reason": "invalid source.json"}, report["skipped"])
        with database(self.erp_root) as db:
            self.assertEqual(0, db.execute(
                "SELECT COUNT(*) FROM products WHERE source_product_id IN ('P000202','P000203')"
            ).fetchone()[0])

    def test_malformed_publication_price_is_reported_without_listing_write(self) -> None:
        publication_path = self.products_root / "P000101/output/store-publications.json"
        publication = json.loads(publication_path.read_text(encoding="utf-8"))
        publication["stores"]["store-a"]["sku_publications"][0]["initial_price_rub"] = {"bad": "shape"}
        write_json(publication_path, publication)

        report = load_indexer()(self.products_root, self.erp_root)

        self.assertEqual(0, report["indexed"]["listings"])
        self.assertIn("invalid sku publication", {row["reason"] for row in report["skipped"]})
        with database(self.erp_root) as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM marketplace_listings").fetchone()[0])

    def test_existing_sku_bound_to_other_product_is_not_reparented(self) -> None:
        self.repo.upsert_product({"id": "OTHER-PRODUCT", "title_cn": "其他商品"})
        self.repo.upsert_sku({
            "id": "LEGACY_SKU:P000101:SKU-101", "product_id": "OTHER-PRODUCT",
            "seller_sku": "EXISTING-SKU",
        })

        report = load_indexer()(self.products_root, self.erp_root)

        with database(self.erp_root) as db:
            sku = db.execute(
                "SELECT product_id, seller_sku FROM skus WHERE id='LEGACY_SKU:P000101:SKU-101'"
            ).fetchone()
            listings = db.execute("SELECT COUNT(*) FROM marketplace_listings").fetchone()[0]
        self.assertEqual(("OTHER-PRODUCT", "EXISTING-SKU"), tuple(sku))
        self.assertEqual(0, listings)
        self.assertIn("existing sku conflict", {row["reason"] for row in report["skipped"]})

    def test_other_organization_store_is_reported_without_listing_write(self) -> None:
        with database(self.erp_root) as db:
            db.execute("INSERT INTO organizations(id, name, created_at) VALUES ('other','Other','2026-01-01')")
            db.execute("""INSERT INTO stores(id,platform,display_name,currency,connection_status,
                       credential_ref,created_at,updated_at,organization_id)
                       VALUES ('other-store','OZON','Other Store','RUB','UNKNOWN','',
                               '2026-01-01','2026-01-01','other')""")
        publication_path = self.products_root / "P000101/output/store-publications.json"
        publication = json.loads(publication_path.read_text(encoding="utf-8"))
        publication["stores"] = {"other-store": publication["stores"]["store-a"]}
        write_json(publication_path, publication)

        report = load_indexer()(self.products_root, self.erp_root)

        self.assertEqual(0, report["indexed"]["listings"])
        self.assertIn("store organization mismatch", {row["reason"] for row in report["skipped"]})
        with database(self.erp_root) as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM marketplace_listings").fetchone()[0])

    def test_indexer_never_uses_conflict_upserts_for_legacy_rows(self) -> None:
        before = artifact_hashes(self.products_root)
        with (patch.object(ErpRepository, "upsert_product", side_effect=AssertionError("product conflict upsert")),
              patch.object(ErpRepository, "upsert_sku", side_effect=AssertionError("sku conflict upsert")),
              patch.object(ErpRepository, "upsert_listing", side_effect=AssertionError("listing conflict upsert"))):
            report = load_indexer()(self.products_root, self.erp_root)

        self.assertEqual({"products": 1, "skus": 1, "listings": 1}, report["indexed"])
        self.assertEqual(before, artifact_hashes(self.products_root))

    def test_existing_remote_identity_under_other_offer_is_not_overwritten(self) -> None:
        self.repo.upsert_product({"id": "OZON:store-a:9001", "title_cn": "已同步商品"})
        self.repo.upsert_listing({
            "store_id": "store-a", "product_id": "OZON:store-a:9001",
            "offer_id": "OTHER-OFFER", "ozon_product_id": "9001",
            "listing_status": "VISIBLE",
        })

        report = load_indexer()(self.products_root, self.erp_root)

        self.assertEqual(0, report["indexed"]["listings"])
        self.assertIn("external identity conflict", {row["reason"] for row in report["skipped"]})
        with database(self.erp_root) as db:
            self.assertEqual([("OTHER-OFFER", "OZON:store-a:9001")], [tuple(row) for row in db.execute(
                "SELECT offer_id,product_id FROM marketplace_listings WHERE store_id='store-a'"
            )])

    def test_cli_requires_explicit_products_and_erp_roots(self) -> None:
        script = Path(__file__).resolve().parents[1] / "scripts/index_legacy_products.py"
        self.assertTrue(script.is_file(), "legacy product index CLI is not implemented")
        result = subprocess.run(
            [sys.executable, str(script)],
            cwd=script.parents[1],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("--products-root", result.stderr)
        self.assertIn("--erp-root", result.stderr)


if __name__ == "__main__":
    unittest.main()
