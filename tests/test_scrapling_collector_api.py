"""Local API contracts; all files are disposable and all outbound IO is mocked."""
import importlib.util
import builtins
import asyncio
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException, Request
from tests.test_scrapling_collector import DATA, URL, document
from tests.test_stage2_collector import PNG_BYTES

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("scrapling_test_app", ROOT / "collector/local-ingest/app.py")
app = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(app)


class ScraplingApiTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(hasattr(app, "preview_scrapling"), "ERP capture API has not been connected")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root_patch = patch.object(app, "ROOT", Path(self.temp.name))
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.operator = {"id": "owner", "is_host_device": True, "role": "owner"}
        self.owner_patch = patch.object(app, "current_operator", return_value=self.operator)
        self.owner_patch.start()
        self.addCleanup(self.owner_patch.stop)

    def test_missing_collector_dependency_stops_with_actionable_error_and_no_capture(self):
        original_import = builtins.__import__
        def missing(name, *args, **kwargs):
            if name.startswith("scrapling"):
                raise ModuleNotFoundError("No module named scrapling", name="scrapling")
            return original_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=missing):
            with self.assertRaises(HTTPException) as error:
                app.preview_scrapling({"url": URL, "html": document(DATA)})
        self.assertEqual(error.exception.status_code, 503)
        self.assertIn("requirements-collector.txt", error.exception.detail)
        self.assertFalse((Path(self.temp.name) / "runtime/collector-captures").exists())

    def test_public_preview_without_dependency_is_503_and_saves_nothing(self):
        original_import = builtins.__import__
        def missing(name, *args, **kwargs):
            if name.startswith("scrapling"):
                raise ModuleNotFoundError("No module named scrapling", name="scrapling")
            return original_import(name, *args, **kwargs)
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]), patch("builtins.__import__", side_effect=missing):
            with self.assertRaises(HTTPException) as error:
                app.preview_scrapling({"url": URL})
        self.assertEqual(error.exception.status_code, 503)
        self.assertIn("requirements-collector.txt", error.exception.detail)
        self.assertFalse((Path(self.temp.name) / "runtime/collector-captures").exists())

    def test_invalid_input_is_422_even_when_dependency_is_missing(self):
        original_import = builtins.__import__
        def missing(name, *args, **kwargs):
            if name.startswith("scrapling"):
                raise ModuleNotFoundError("No module named scrapling", name="scrapling")
            return original_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=missing):
            for payload in ({"url": "https://seller.ozon.ru/app/dashboard/main"}, {"url": URL, "html": 123}):
                with self.assertRaises(HTTPException) as error:
                    app.preview_scrapling(payload)
                self.assertEqual(error.exception.status_code, 422)
        self.assertFalse((Path(self.temp.name) / "runtime/collector-captures").exists())

    def test_preview_and_import_bind_to_server_snapshot_without_trusting_replacement_facts(self):
        preview = app.preview_scrapling({"url": URL, "html": document(DATA)})
        self.assertEqual(preview["capture_method"], "user_supplied_html")
        selected = {"capture_id": preview["capture_id"], "selected_sku_ids": ["10002"], "category_id": 1, "type_id": 2}
        with patch.object(app, "prepare_rules", return_value={"attributes": []}), patch.object(app, "ingest_capture", return_value={"product_id": "P000001"}) as ingest:
            result = app.import_scrapling(selected)
            duplicate = app.import_scrapling(selected)
        self.assertEqual(result["product_id"], "P000001")
        self.assertEqual(duplicate["product_id"], "P000001")
        self.assertEqual(ingest.call_count, 1)
        self.assertEqual([s["sku_id"] for s in ingest.call_args.args[0]["skus"]], ["10002"])
        self.assertEqual(ingest.call_args.args[0]["raw_snapshot"]["capture_method"], "user_supplied_html")
        with self.assertRaises(HTTPException) as error:
            app.import_scrapling({**selected, "capture": {"title_cn": "篡改"}})
        self.assertEqual(error.exception.status_code, 422)

    def test_foreign_owner_and_non_host_are_refused(self):
        preview = app.preview_scrapling({"url": URL, "html": document(DATA)})
        self.operator["id"] = "other-owner"
        with self.assertRaises(HTTPException) as error:
            app.import_scrapling({"capture_id": preview["capture_id"], "selected_sku_ids": ["10002"], "category_id": 1, "type_id": 2})
        self.assertEqual(error.exception.status_code, 403)
        self.operator["is_host_device"] = False
        with self.assertRaises(HTTPException) as error:
            app.preview_scrapling({"url": URL, "html": document(DATA)})
        self.assertEqual(error.exception.status_code, 403)

    def test_missing_sku_and_out_of_stock_cannot_be_imported(self):
        preview = app.preview_scrapling({"url": URL, "html": document(DATA)})
        for sku in ("99999", "10001"):
            with self.assertRaises(HTTPException) as error:
                app.import_scrapling({"capture_id": preview["capture_id"], "selected_sku_ids": [sku], "category_id": 1, "type_id": 2})
            self.assertEqual(error.exception.status_code, 422)

    def test_duplicate_source_returns_readable_message_without_overwriting_existing_product(self):
        preview = app.preview_scrapling({"url": URL, "html": document(DATA)})
        with patch.object(app, "prepare_rules", return_value={"attributes": []}), patch.object(app, "ingest_capture", side_effect=HTTPException(status_code=409, detail={"message": "该产品已经采集过。", "existing_product_id": "P000001"})):
            with self.assertRaises(HTTPException) as error:
                app.import_scrapling({"capture_id": preview["capture_id"], "selected_sku_ids": ["10002"], "category_id": 1, "type_id": 2})
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(error.exception.detail, "该产品已经采集过。 已有商品：P000001")

    def test_preview_import_and_reopen_use_one_collector_product(self):
        from scripts.collector_categories import prepare_rules
        metadata = Path("ozon-adapter/metadata/ozon-rules-2026-07-10")
        target = Path(self.temp.name) / metadata
        target.mkdir(parents=True)
        for name in ("categories.json", "category-tree.zh-CN.json"):
            shutil.copy2(ROOT / metadata / name, target / name)
        rules = prepare_rules(ROOT, 17027907, 94462, allow_fetch=False)
        products = Path(self.temp.name) / "products"
        products.mkdir()
        with patch.object(app, "PRODUCTS_DIR", products), patch.object(app, "prepare_rules", return_value=rules), \
             patch.object(app, "download_url", return_value=(PNG_BYTES, "image/png")):
            preview = app.preview_scrapling({"url": URL, "html": document(DATA)})
            selection = {"capture_id": preview["capture_id"], "selected_sku_ids": ["10002"],
                         "category_id": 17027907, "type_id": 94462}
            saved = app.import_scrapling(selection)
            self.assertEqual(app.import_scrapling(selection), saved)
            detail = app.workbench_product(saved["product_id"])
            catalog = detail["captured_sku_catalog"]
            self.assertEqual(
                [(item["sku_id"], item["selected"], item["availability"]) for item in catalog],
                [("10001", False, "out_of_stock"), ("10002", True, "unknown")],
            )
            self.assertEqual(catalog[0]["option_values"][0]["value_cn"], "黑色")
            self.assertEqual(catalog[0]["image_url"], "https://cbu01.alicdn.com/img/black.jpg")
            self.assertEqual(catalog[0]["purchase_price"], 39.5)
            self.operator["organization_id"] = "foreign-organization"
            with self.assertRaises(HTTPException) as error:
                app.workbench_product(saved["product_id"])
        self.assertEqual(error.exception.status_code, 403)
        self.assertEqual(len(list(products.glob("P*/input/source.json"))), 1)
        source = json.loads((products / saved["product_id"] / "input/source.json").read_text(encoding="utf-8"))
        raw = json.loads((products / saved["product_id"] / "input/raw-snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(source["source_url"], URL)
        self.assertEqual(source["title_cn"], DATA["offerTitle"])
        self.assertEqual(source["skus"][0]["sku_id"], "10002")
        self.assertEqual(source["skus"][0]["option_values"][0]["value_cn"], "银色")
        self.assertTrue(source["skus"][0]["local_image_path"])
        self.assertEqual(raw["raw_snapshot"]["capture_method"], "user_supplied_html")
        self.assertEqual(detail["source"]["title_cn"], DATA["offerTitle"])
        self.assertFalse(detail["production_readiness"]["blocking"], detail["production_readiness"])

    def test_incomplete_scrapling_facts_block_production_after_import(self):
        product = Path(self.temp.name) / "products/P000001"
        (product / "input").mkdir(parents=True)
        (product / "input/source.json").write_text(json.dumps({
            "source_kind": "workbench_collection", "title_cn": "铝合金折叠桌", "main_images": [],
            "skus": [{"sku_id": "10002", "purchase_price": None, "sku_image_missing": True}],
        }), encoding="utf-8")
        (product / "input/raw-snapshot.json").write_text(json.dumps({"plugin_version": "scrapling-0.4.15-erp-v1"}), encoding="utf-8")
        with patch.object(app, "validate_formal_product_input", return_value={}), \
             patch.object(app, "asset_boundaries_enabled", return_value=True):
            readiness = app.production_readiness_state(product, {"status": "COLLECTED"}, {})
        self.assertTrue(readiness["blocking"])
        self.assertIn("采购价", readiness["message"])
        self.assertIn("主图", readiness["message"])
        self.assertIn("规格图片", readiness["message"])

    def test_failed_image_download_blocks_detail_and_formal_batch_without_losing_capture(self):
        from scripts.collector_categories import prepare_rules
        from scripts.pipeline_runtime import create_batch
        metadata = Path("ozon-adapter/metadata/ozon-rules-2026-07-10")
        target = Path(self.temp.name) / metadata
        target.mkdir(parents=True)
        for name in ("categories.json", "category-tree.zh-CN.json"):
            shutil.copy2(ROOT / metadata / name, target / name)
        rules = prepare_rules(ROOT, 17027907, 94462, allow_fetch=False)
        products = Path(self.temp.name) / "products"
        products.mkdir()
        with patch.object(app, "PRODUCTS_DIR", products), patch.object(app, "prepare_rules", return_value=rules), \
             patch.object(app, "download_url", side_effect=OSError("offline image failure")):
            preview = app.preview_scrapling({"url": URL, "html": document(DATA)})
            saved = app.import_scrapling({"capture_id": preview["capture_id"], "selected_sku_ids": ["10002"],
                                         "category_id": 17027907, "type_id": 94462})
            product = products / saved["product_id"]
            source_before = (product / "input/source.json").read_bytes()
            detail = app.workbench_product(saved["product_id"])
            with self.subTest("detail"):
                self.assertTrue(detail["production_readiness"]["blocking"], detail["production_readiness"])
                self.assertIn("主图", detail["production_readiness"]["message"])
                self.assertIn("规格图片", detail["production_readiness"]["message"])
            async def receive():
                return {"type": "http.request", "body": b"{}", "more_body": False}
            request = Request({"type": "http", "method": "POST", "path": "/"}, receive)
            with self.subTest("single run"), patch.object(app, "launch_or_enqueue_batch") as launch:
                with self.assertRaises(HTTPException) as error:
                    asyncio.run(app.run_single_workbench_product(saved["product_id"], request))
                self.assertEqual(error.exception.status_code, 422)
                self.assertIn("采集资料", str(error.exception.detail))
                launch.assert_not_called()
            with self.subTest("batch"):
                with self.assertRaisesRegex(ValueError, "采集资料"):
                    create_batch(Path(self.temp.name), [saved["product_id"]])
            self.assertEqual((product / "input/source.json").read_bytes(), source_before)
            self.assertFalse(list((Path(self.temp.name) / "batches").glob("B-*")))


if __name__ == "__main__":
    unittest.main()
