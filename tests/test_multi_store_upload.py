import json
import tempfile
import subprocess
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from scripts.multi_store_upload import (
    default_runner,
    execute_selected_stores,
    aggregate_product_status,
    image_repair_retryable,
    ozon_issue_bucket,
    prepare_isolated_product,
    refresh_pending_stores,
    summarize_ozon_issues,
    variant_repair_retryable,
)
from scripts.store_publications import ensure_store_offer_ids, load_publications, select_stores, save_publications
from scripts.task_database import cutover_to_sqlite
from scripts.workbench_stores import list_stores
from tests.test_batch_upload_approval import make_ready_product
from scripts import batch_upload_approval as approval
from scripts import multi_store_upload as uploader


def write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def make_product(root: Path) -> Path:
    product = root / "products/P000001"
    write(product / "input/source.json", {
        "product_id": "P000001", "title_cn": "测试商品",
        "skus": [{"sku_id": "sku-a", "sku_name": "白色", "purchase_price": 10}],
    })
    write(product / "status.json", {
        "product_id": "P000001", "status": "WAITING_MANUAL_REVIEW", "completed_steps": ["collect_source", "field_completion"],
        "pending_steps": ["ozon_upload"], "next_action": "ozon_upload", "steps": [], "history": [],
        "task_authorized": True, "api_write_count": 0, "ozon": {},
    })
    write(product / "output/pricing-result.json", {
        "sku_pricing": [{"sku_id": "sku-a", "selling_price_cny": 50, "selling_price_rub": 600}],
    })
    write(product / "output/ozon-upload-config.json", {
        "shop_name": "default", "sku_prices": [{"source_sku_id": "sku-a", "price": "50.00"}],
    })
    write(product / "output/ozon-category.json", {
        "category_id": 1, "type_id": 2, "category_name": "Тест",
        "match_status": "api_confirmed", "confidence": 1.0, "metadata_source": "ozon_seller_api",
    })
    write(product / "output/ozon-category-attributes.json", {
        "category_id": 1, "type_id": 2, "attributes": [],
    })
    return product


class ApprovalStoreBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        make_ready_product(self.root, "P000001", "C-1", "sku-a", ["store-a", "store-b"])
        self.product = self.root / "products/P000001"
        source = json.loads((self.product / "input/source.json").read_text(encoding="utf-8"))
        source["skus"] = [{"sku_id": "sku-a", "sku_name": "white", "purchase_price": 10}]
        write(self.product / "input/source.json", source)
        draft = json.loads((self.product / "output/ozon-draft.json").read_text(encoding="utf-8"))
        draft["skus"][0]["source_sku_id"] = "sku-a"
        write(self.product / "output/ozon-draft.json", draft)
        write(self.product / "output/ozon-tags.json", {"tags": ["#товар", "#покупка"]})
        status = json.loads((self.product / "status.json").read_text(encoding="utf-8"))
        status["batch_id"] = "B-STORE"
        write(self.product / "status.json", status)
        select_stores(self.product, ["store-a", "store-b"], ["store-a", "store-b"])
        ensure_store_offer_ids(self.product)
        self.batch = {"batch_id": "B-STORE", "products": [{"product_id": "P000001", "collection_id": "C-1", "target_store_ids": ["store-a", "store-b"]}]}
        write(self.root / "batches/B-STORE/batch.json", self.batch)
        self.calls = []
        self.addCleanup(patch.stopall)
        patch.object(uploader, "_field_completion_build_package", side_effect=AssertionError("No payload rebuild during approved upload")).start()
        patch("socket.create_connection", side_effect=AssertionError("No network in boundary tests")).start()

    def authorize(self):
        return approval.authorize_batch_upload(self.root, self.batch, approval.compute_batch_review_hash(self.root, self.batch), "operator")

    def runner(self, root, isolated, store_id):
        self.calls.append(store_id)
        return {"returncode": 0, "status": {"status": "SUBMITTED", "api_write_count": 1}, "result": {"task_id": "task-" + store_id, "items": [{"source_sku_id": "sku-a", "task_id": "task-" + store_id}]}}

    def test_missing_authorization_starts_no_store(self):
        result = execute_selected_stores(self.root, self.product, runner=self.runner)
        self.assertEqual(self.calls, [])
        self.assertEqual(result["status"], "BLOCKED_BEFORE_UPLOAD")

    def test_blocked_command_has_nonzero_exit_status(self):
        with patch.object(uploader, "ROOT", self.root), patch("sys.argv", ["multi_store_upload.py", str(self.product), "--execute"]):
            self.assertNotEqual(uploader.main(), 0)

    def test_actual_runner_holds_file_lock(self):
        self.authorize()
        def locked_runner(root, isolated, store_id):
            child = subprocess.run([sys.executable, "-c", "from pathlib import Path; from scripts.batch_upload_approval import batch_upload_lock; import sys\nwith batch_upload_lock(Path(sys.argv[1])): print('unexpected')", str(root)], cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=10)
            self.assertNotEqual(child.returncode, 0)
            self.assertIn(b"BatchUploadBusy", child.stderr)
            return self.runner(root, isolated, store_id)
        execute_selected_stores(self.root, self.product, runner=locked_runner)
        self.assertEqual(self.calls, ["store-a", "store-b"])

    def test_contended_upload_does_not_overwrite_active_status(self):
        self.authorize()
        before = (self.product / "status.json").read_bytes()
        with patch.object(uploader, "batch_upload_lock", side_effect=uploader.BatchUploadBusy("held by another process")):
            result = execute_selected_stores(self.root, self.product, runner=self.runner)
        self.assertEqual(self.calls, [])
        self.assertEqual(result["status"], "BLOCKED_BEFORE_UPLOAD")
        self.assertEqual((self.product / "status.json").read_bytes(), before)

    def test_runner_exception_preserves_task_evidence_and_blocks_following_store(self):
        self.authorize()
        def interrupted(root, isolated, store_id):
            write(isolated / "output/ozon-result.json", {"task_id": "accepted-before-crash"})
            write(isolated / "status.json", {"status": "SUBMITTED", "api_write_count": 1})
            raise RuntimeError("interrupted after receipt")
        result = execute_selected_stores(self.root, self.product, runner=interrupted)
        saved = load_publications(self.product)["stores"]["store-a"]
        self.assertEqual(saved["sku_publications"][0]["task_id"], "accepted-before-crash")
        self.assertEqual(result["api_write_count"], 1)
        self.assertEqual(result["status"], "BLOCKED_BEFORE_UPLOAD")

    def test_edit_between_stores_blocks_second_and_keeps_sqlite_task(self):
        self.assertTrue(callable(getattr(uploader, "batch_upload_lock", None)))
        cutover_to_sqlite(self.root)
        self.authorize()
        original_lock = uploader.batch_upload_lock
        completed = 0

        @contextmanager
        def edit_after_store(root):
            nonlocal completed
            with original_lock(root):
                yield
            if self.calls and not completed:
                completed += 1
                with approval.protected_batch_mutation(root, "P000001", "draft_saved"):
                    write(self.product / "output/workbench-draft.json", {"title_ru": "changed"})

        with patch.object(uploader, "batch_upload_lock", side_effect=edit_after_store):
            result = execute_selected_stores(self.root, self.product, runner=self.runner)
        self.assertEqual(self.calls, ["store-a"])
        self.assertEqual(result["status"], "BLOCKED_BEFORE_UPLOAD")
        stores = load_publications(self.product)["stores"]
        self.assertEqual(stores["store-a"]["sku_publications"][0]["task_id"], "task-store-a")
        self.assertEqual(stores["store-b"]["status"], "SELECTED")
        self.assertEqual(result["api_write_count"], 1)

    def test_failed_same_hash_retry_and_payload_change_denial(self):
        self.authorize()
        def fail(root, isolated, store_id):
            self.calls.append(store_id)
            return {"returncode": 1, "status": {"status": "NEEDS_ATTENTION", "api_write_count": 0, "error_message": "transport failed"}, "result": {}}
        config_before = (self.product / "output/ozon-upload-config.json").read_bytes()
        execute_selected_stores(self.root, self.product, runner=fail)
        execute_selected_stores(self.root, self.product, only_store_ids=["store-a"], runner=self.runner)
        self.assertEqual(self.calls, ["store-a", "store-b", "store-a"])
        self.assertEqual((self.product / "output/ozon-upload-config.json").read_bytes(), config_before)
        write(self.product / "output/copy-ru.json", {"title_ru": "changed payload"})
        result = execute_selected_stores(self.root, self.product, only_store_ids=["store-b"], runner=self.runner)
        self.assertEqual(self.calls, ["store-a", "store-b", "store-a"])
        self.assertEqual(result["status"], "BLOCKED_BEFORE_UPLOAD")

    def test_pending_success_task_id_and_ambiguous_records_are_never_retried(self):
        self.authorize()
        for state, evidence in (("PENDING_REMOTE", {}), ("SUCCESS", {}), ("FAILED", {"task_id": "remote-task"}), ("QUERY_ERROR", {}), ("FAILED", {"api_write_count": 1}), ("FAILED", {"sku_publications": [{"sku_id": "sku-a", "moderation_status": "SUCCESS"}]})):
            with self.subTest(state=state, evidence=evidence):
                data = load_publications(self.product)
                data["stores"]["store-a"].update(status=state)
                data["stores"]["store-a"].update(evidence)
                save_publications(self.product, data)
                execute_selected_stores(self.root, self.product, only_store_ids=["store-a"], runner=self.runner)
                self.assertEqual(self.calls, [])

    def test_task_id_or_ambiguous_store_blocks_command_without_changing_evidence(self):
        self.authorize()
        for state, task_id in (("FAILED", "remote-task"), ("QUERY_ERROR", "unknown")):
            with self.subTest(state=state):
                data = load_publications(self.product)
                record = data["stores"]["store-a"]
                record["status"] = state
                record["sku_publications"][0]["task_id"] = task_id
                save_publications(self.product, data)
                status_before = (self.product / "status.json").read_bytes()
                with patch.object(uploader, "ROOT", self.root), patch("sys.argv", ["multi_store_upload.py", str(self.product), "--execute", "--only-store", "store-a"]), patch.object(uploader, "default_runner", side_effect=AssertionError("runner must not start")):
                    self.assertEqual(uploader.main(), 3)
                self.assertEqual((self.product / "status.json").read_bytes(), status_before)
                saved = load_publications(self.product)["stores"]["store-a"]
                self.assertEqual(saved["status"], state)
                self.assertEqual(saved["sku_publications"][0]["task_id"], task_id)

    def test_sqlite_sync_failure_blocks_before_runner_and_preserves_task_evidence(self):
        cutover_to_sqlite(self.root)
        data = load_publications(self.product)
        data["stores"]["store-a"]["status"] = "FAILED"
        data["stores"]["store-a"]["sku_publications"][0]["task_id"] = "kept-task"
        save_publications(self.product, data)
        self.authorize()
        status_before = (self.product / "status.json").read_bytes()
        publications_before = load_publications(self.product)
        with patch.dict(sys.modules, {"task_database": sys.modules["scripts.task_database"]}), patch("scripts.task_database.sync_publications_json", side_effect=OSError("SQLite sync failed")):
            result = execute_selected_stores(self.root, self.product, only_store_ids=["store-b"], runner=self.runner)
        self.assertEqual(result["status"], "BLOCKED_BEFORE_UPLOAD")
        self.assertEqual(self.calls, [])
        self.assertEqual((self.product / "status.json").read_bytes(), status_before)
        saved = load_publications(self.product)
        self.assertEqual(saved["stores"]["store-a"]["sku_publications"][0]["task_id"], "kept-task")
        self.assertEqual(saved["stores"]["store-b"]["status"], publications_before["stores"]["store-b"]["status"])

    def test_sqlite_sync_failure_starts_neither_selected_store_runner(self):
        cutover_to_sqlite(self.root)
        self.authorize()
        status_before = (self.product / "status.json").read_bytes()
        with patch.dict(sys.modules, {"task_database": sys.modules["scripts.task_database"]}), patch("scripts.task_database.sync_publications_json", side_effect=OSError("SQLite sync failed")):
            result = execute_selected_stores(self.root, self.product, runner=self.runner)
        self.assertEqual(result["status"], "BLOCKED_BEFORE_UPLOAD")
        self.assertEqual(result["skipped"], [{"store_id": "store-a", "reason": "publication_persistence_failed"}])
        self.assertEqual(self.calls, [])
        self.assertEqual((self.product / "status.json").read_bytes(), status_before)
        self.assertEqual({store["status"] for store in load_publications(self.product)["stores"].values()}, {"SELECTED"})

    def test_store_override_changes_hash_without_runtime_status_changing_hash(self):
        before = approval.compute_batch_review_hash(self.root, self.batch)
        data = load_publications(self.product)
        data["stores"]["store-a"]["status"] = "FAILED"
        save_publications(self.product, data)
        self.assertEqual(approval.compute_batch_review_hash(self.root, self.batch), before)
        data["stores"]["store-a"]["sku_publications"][0]["price_override_cny"] = 123
        save_publications(self.product, data)
        self.assertNotEqual(approval.compute_batch_review_hash(self.root, self.batch), before)


class MultiStoreUploadTest(unittest.TestCase):
    def setUp(self):
        # These existing tests isolate result projection/config preparation.
        # ApprovalStoreBoundaryTest above exercises the real authorization gate.
        guard = patch.object(uploader, "store_upload_is_authorized", return_value=True)
        guard.start()
        self.addCleanup(guard.stop)

    def test_offer_ids_are_unique_per_store_sku_and_stable_across_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = make_product(root)
            source = json.loads((product / "input/source.json").read_text(encoding="utf-8"))
            source["skus"].append({
                "sku_id": "sku-b", "sku_name": "黑色", "purchase_price": 12,
            })
            write(product / "input/source.json", source)
            select_stores(product, ["store-a", "store-b"], ["store-a", "store-b"])

            first = ensure_store_offer_ids(product)
            first_map = {
                (store_id, sku["sku_id"]): sku["offer_id"]
                for store_id, record in first["stores"].items()
                for sku in record["sku_publications"]
            }
            self.assertEqual(len(first_map), 4)
            self.assertEqual(len(set(first_map.values())), 4)
            self.assertTrue(all(len(value) == 16 and value.isascii() for value in first_map.values()))

            second = ensure_store_offer_ids(product)
            second_map = {
                (store_id, sku["sku_id"]): sku["offer_id"]
                for store_id, record in second["stores"].items()
                for sku in record["sku_publications"]
            }
            self.assertEqual(second_map, first_map)

    def test_existing_task_offer_id_is_never_changed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = make_product(root)
            data = select_stores(product, ["store-a"], ["store-a"])
            sku = data["stores"]["store-a"]["sku_publications"][0]
            sku.update({"offer_id": "LEGACY-OFFER", "task_id": "70001"})
            write(product / "output/store-publications.json", data)
            source = json.loads((product / "input/source.json").read_text(encoding="utf-8"))
            source["skus"].append({
                "sku_id": "sku-late", "sku_name": "不应补入", "purchase_price": 20,
            })
            write(product / "input/source.json", source)

            persisted = ensure_store_offer_ids(product)
            locked = persisted["stores"]["store-a"]["sku_publications"][0]
            self.assertEqual(locked["offer_id"], "LEGACY-OFFER")
            self.assertEqual(locked["task_id"], "70001")
            self.assertEqual(len(persisted["stores"]["store-a"]["sku_publications"]), 1)

    def test_offer_id_mapping_remains_stable_after_sqlite_cutover(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = make_product(root)
            select_stores(product, ["store-a", "store-b"], ["store-a", "store-b"])
            cutover_to_sqlite(root)

            first = ensure_store_offer_ids(product)
            first_map = {
                store_id: record["sku_publications"][0]["offer_id"]
                for store_id, record in first["stores"].items()
            }
            second = ensure_store_offer_ids(product)
            second_map = {
                store_id: record["sku_publications"][0]["offer_id"]
                for store_id, record in second["stores"].items()
            }
            self.assertEqual(first_map, second_map)
            self.assertEqual(len(set(first_map.values())), 2)

    def test_synchronous_upload_materializes_handoff_snapshot_after_sqlite_cutover(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = make_product(root)
            select_stores(product, ["store-a"], ["store-a"])
            cutover_to_sqlite(root)

            def runner(_root, _isolated, _store_id):
                return {
                    "returncode": 0,
                    "status": {"status": "SUBMITTED", "api_write_count": 1, "ozon": {}},
                    "result": {
                        "task_id": "task-a", "action": "create",
                        "items": [{
                            "source_sku_id": "sku-a", "offer_id": "offer-a",
                            "task_id": "task-a",
                        }],
                    },
                    "idempotency": {"payload_hash": "hash-a"},
                }

            result = execute_selected_stores(root, product, runner=runner)
            snapshot = json.loads((product / "status.json").read_text(encoding="utf-8"))

            self.assertEqual(result["status"], "PENDING_REMOTE")
            self.assertEqual(snapshot["status"], "PENDING_REMOTE")
            self.assertEqual(snapshot["api_write_count"], 1)
            self.assertEqual(snapshot["ozon"]["task_id"], "task-a")
            self.assertEqual(snapshot["completed_at"], "unknown")
            self.assertEqual(snapshot["upload_priority_state"], "waiting_remote")
            self.assertFalse(snapshot["task_authorized"])

    def test_unwritten_selected_store_keeps_product_partial(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = make_product(root)
            select_stores(product, ["store-a", "store-b"], ["store-a", "store-b"])

            def runner(_root, _isolated, _store_id):
                return {
                    "returncode": 0,
                    "status": {"status": "SUBMITTED", "api_write_count": 1, "ozon": {}},
                    "result": {
                        "task_id": "task-a", "action": "create",
                        "items": [{
                            "source_sku_id": "sku-a", "offer_id": "offer-a",
                            "task_id": "task-a",
                        }],
                    },
                    "idempotency": {"payload_hash": "hash-a"},
                }

            result = execute_selected_stores(root, product, only_store_ids=["store-a"], runner=runner)
            snapshot = json.loads((product / "status.json").read_text(encoding="utf-8"))
            stores = load_publications(product)["stores"]

            self.assertEqual(result["status"], "PARTIAL")
            self.assertEqual(snapshot["status"], "PARTIAL")
            self.assertEqual(snapshot["next_action"], "ozon_upload")
            self.assertEqual(stores["store-a"]["status"], "PENDING_REMOTE")
            self.assertEqual(stores["store-b"]["status"], "SELECTED")

    def test_isolated_store_uses_persisted_offer_ids_and_requires_create(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = make_product(root)
            write(product / "output/ozon-draft.json", {
                "offer_id": "P000001-draft",
                "skus": [{"source_sku_id": "sku-a", "offer_id": "P000001-sku-a"}],
            })
            write(product / "output/variant-grouping-result.json", {
                "variants": [{"sku_id": "sku-a", "offer_id": "P000001-sku-a"}],
            })
            selected = select_stores(product, ["store-a"], ["store-a"])
            selected = ensure_store_offer_ids(product)
            record = selected["stores"]["store-a"]
            assigned = record["sku_publications"][0]["offer_id"]

            isolated = prepare_isolated_product(root, product, "store-a", record)
            draft = json.loads((isolated / "output/ozon-draft.json").read_text(encoding="utf-8"))
            grouping = json.loads((isolated / "output/variant-grouping-result.json").read_text(encoding="utf-8"))
            marker = json.loads((isolated / "output/store-offer-id-map.json").read_text(encoding="utf-8"))
            self.assertEqual(draft["offer_id"], assigned)
            self.assertEqual(draft["skus"][0]["offer_id"], assigned)
            self.assertEqual(grouping["variants"][0]["offer_id"], assigned)
            self.assertTrue(marker["requires_create"])

    def test_missing_upload_config_is_materialized_before_store_workspace_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = make_product(root)
            (product / "output/ozon-upload-config.json").unlink()
            write(product / "output/ozon-draft.json", {
                "offer_id": "P000001-draft",
                "skus": [{"source_sku_id": "sku-a", "offer_id": "P000001-sku-a"}],
            })
            select_stores(product, ["store-a"], ["store-a"])
            selected = ensure_store_offer_ids(product)
            record = selected["stores"]["store-a"]

            def fake_build_package(target, write=True, pre_image=False):
                globals()["write"](target / "output/ozon-upload-config.json", {
                    "shop_name": "default",
                    "sku_prices": [{"source_sku_id": "sku-a", "price": "50.00"}],
                })
                globals()["write"](target / "output/ozon-tags.json", {
                    "tags": [
                        "#канистра", "#топливо", "#металл", "#сталь", "#гараж", "#мастерская",
                        "#техника", "#емкость", "#хранение", "#переноска", "#ручка", "#крышка",
                        "#автотовары", "#поездка", "#дача", "#запас", "#закрытая", "#квадратная",
                        "#прочная", "#большая", "#удобная", "#практичная", "#покупка", "#товар",
                        "#дом", "#работа", "#сервис", "#резерв", "#комплект", "#выбор",
                    ],
                })
                return {}

            with patch("scripts.multi_store_upload._field_completion_build_package", return_value=fake_build_package):
                isolated = prepare_isolated_product(root, product, "store-a", record)

            self.assertTrue((product / "output/ozon-upload-config.json").is_file())
            self.assertTrue((isolated / "output/ozon-upload-config.json").is_file())

    def test_blocked_color_policy_is_refreshed_before_store_workspace_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = make_product(root)
            write(product / "output/ozon-draft.json", {
                "offer_id": "P000001-draft",
                "skus": [{"source_sku_id": "sku-a", "offer_id": "P000001-sku-a"}],
            })
            write(product / "output/ozon-tags.json", {"tags": ["#товар"]})
            write(product / "output/color-variant-policy.json", {
                "status": "BLOCK",
                "blocking_variants": [{"sku_id": "sku-a"}],
            })
            select_stores(product, ["store-a"], ["store-a"])
            selected = ensure_store_offer_ids(product)
            record = selected["stores"]["store-a"]

            def fake_build_package(target, write=True, pre_image=False):
                globals()["write"](target / "output/ozon-upload-config.json", {
                    "shop_name": "default",
                    "sku_prices": [{"source_sku_id": "sku-a", "price": "50.00"}],
                })
                globals()["write"](target / "output/ozon-tags.json", {"tags": ["#товар"]})
                globals()["write"](target / "output/color-variant-policy.json", {"status": "PASS"})
                return {}

            with patch("scripts.multi_store_upload._field_completion_build_package", return_value=fake_build_package) as loader:
                isolated = prepare_isolated_product(root, product, "store-a", record)

            self.assertTrue(loader.called)
            self.assertEqual(
                json.loads((product / "output/color-variant-policy.json").read_text(encoding="utf-8"))["status"],
                "PASS",
            )
            self.assertEqual(
                json.loads((isolated / "output/color-variant-policy.json").read_text(encoding="utf-8"))["status"],
                "PASS",
            )

    def test_default_runner_surfaces_prewrite_failure_from_store_log(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            isolated = root / "runtime/products/P000001"
            write(isolated / "status.json", {
                "status": "WAITING_MANUAL_REVIEW", "api_write_count": 0,
                "error_message": "unknown", "ozon": {},
            })
            write(isolated / "output/store-offer-id-map.json", {
                "requires_create": True,
            })

            def failed_run(command, *_args, **kwargs):
                self.assertEqual(command[-3:], ["--require-action", "create", "--execute"])
                kwargs["stdout"].write("FAILED\n- Persistent image channel did not become ready within 60 seconds\n")
                kwargs["stdout"].flush()
                return type("Completed", (), {"returncode": 2})()

            with patch("scripts.multi_store_upload.subprocess.run", side_effect=failed_run):
                outcome = default_runner(root, isolated, "store-a")
            self.assertEqual(outcome["returncode"], 2)
            self.assertEqual(
                outcome["status"]["error_message"],
                "Persistent image channel did not become ready within 60 seconds",
            )

    @unittest.skip(
        "fixture 缺口（2026-08-14 审计 §7）：ensure_upload_config_exists 强制走 field-completion 最终态重建，"
        "需要项目根内的真实图片产物（draft 图、rich-content source_images），临时目录无法构造；"
        "修复方向是抽公共完整产物 fixture 或把重建拆成 config-only 与 full-package 两档"
    )
    def test_remote_image_failure_uses_image_repair_without_new_create(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = make_product(root)
            write(product / "output/ozon-draft.json", {
                "offer_id": "draft-offer",
                "images": [],
                "skus": [{"source_sku_id": "sku-a", "offer_id": "draft-sku"}],
            })
            data = select_stores(product, ["store-a"], ["store-a"])
            record = data["stores"]["store-a"]
            record.update({
                "status": "FAILED",
                "api_write_count": 1,
                "last_error": "all_image_failed",
            })
            record["sku_publications"][0].update({
                "offer_id": "OFFER-A",
                "task_id": "TASK-A",
                "ozon_product_id": "PRODUCT-A",
                "errors": ["pics_http_error"],
            })
            self.assertTrue(image_repair_retryable(record))

            isolated = prepare_isolated_product(root, product, "store-a", record)
            marker = json.loads((isolated / "output/store-offer-id-map.json").read_text(encoding="utf-8"))
            status = json.loads((isolated / "status.json").read_text(encoding="utf-8"))
            self.assertTrue(marker["requires_image_repair"])
            self.assertFalse(marker["requires_create"])
            self.assertEqual(status["status"], "UPLOADED")

            commands = []

            def fake_run(command, *_args, **kwargs):
                commands.append(command)
                kwargs["stdout"].write('{"status":"submitted","task_id":70002}\\n')
                kwargs["stdout"].flush()
                write(isolated / "status.json", {
                    "status": "PENDING_REMOTE",
                    "api_write_count": 2,
                    "ozon": {"task_id": "70002"},
                })
                write(isolated / "output/ozon-result.json", {
                    "status": "submitted",
                    "task_id": "70002",
                    "items": [{"source_sku_id": "sku-a", "offer_id": "OFFER-A", "task_id": "70002"}],
                })
                return type("Completed", (), {"returncode": 0})()

            with patch("scripts.multi_store_upload.subprocess.run", side_effect=fake_run):
                outcome = default_runner(root, isolated, "store-a")

            self.assertEqual(outcome["returncode"], 0)
            self.assertIn("--repair-images", commands[0])
            self.assertIn("--force-image-resubmit", commands[0])
            self.assertNotIn("--execute", commands[0])
            self.assertNotIn("--require-action", commands[0])

    @unittest.skip(
        "fixture 缺口（2026-08-14 审计 §7）：强制最终态重建需要项目根内的真实图片产物，临时目录无法构造"
    )
    def test_remote_variant_failure_uses_update_not_create(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = make_product(root)
            write(product / "output/ozon-draft.json", {
                "offer_id": "draft-offer",
                "images": [],
                "skus": [{"source_sku_id": "sku-a", "offer_id": "draft-sku"}],
            })
            write(product / "output/variant-grouping-result.json", {
                "variants": [{"sku_id": "sku-a", "offer_id": "draft-sku"}],
            })
            data = select_stores(product, ["store-a"], ["store-a"])
            record = data["stores"]["store-a"]
            record.update({
                "status": "FAILED",
                "api_write_count": 1,
                "last_error": "SPU_ALREADY_EXISTS_IN_ANOTHER_ACCOUNT",
            })
            record["sku_publications"][0].update({
                "offer_id": "OFFER-A",
                "task_id": "TASK-A",
                "ozon_product_id": "PRODUCT-A",
                "errors": ["double_without_merger_offer"],
            })
            self.assertTrue(variant_repair_retryable(record))

            isolated = prepare_isolated_product(root, product, "store-a", record)
            marker = json.loads((isolated / "output/store-offer-id-map.json").read_text(encoding="utf-8"))
            status = json.loads((isolated / "status.json").read_text(encoding="utf-8"))
            self.assertTrue(marker["requires_update"])
            self.assertTrue(marker["requires_variant_repair"])
            self.assertFalse(marker["requires_create"])
            self.assertFalse(marker["requires_image_repair"])
            self.assertEqual(status["status"], "WAITING_MANUAL_REVIEW")

            commands = []

            def fake_run(command, *_args, **kwargs):
                commands.append(command)
                kwargs["stdout"].write('{"status":"submitted","task_id":70003}\\n')
                kwargs["stdout"].flush()
                write(isolated / "status.json", {
                    "status": "PENDING_REMOTE",
                    "api_write_count": 1,
                    "ozon": {"task_id": "70003"},
                })
                write(isolated / "output/ozon-result.json", {
                    "status": "submitted",
                    "task_id": "70003",
                    "items": [{"source_sku_id": "sku-a", "offer_id": "OFFER-A", "task_id": "70003"}],
                })
                return type("Completed", (), {"returncode": 0})()

            with patch("scripts.multi_store_upload.subprocess.run", side_effect=fake_run):
                outcome = default_runner(root, isolated, "store-a")

            self.assertEqual(outcome["returncode"], 0)
            self.assertIn("--execute", commands[0])
            self.assertIn("--require-action", commands[0])
            self.assertIn("update", commands[0])
            self.assertNotIn("--repair-images", commands[0])

    @unittest.skip(
        "fixture 缺口（2026-08-14 审计 §7）：强制最终态重建需要项目根内的真实图片产物，临时目录无法构造"
    )
    def test_store_results_are_isolated_and_handed_off_store_is_never_resent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = make_product(root)
            select_stores(product, ["store-a", "store-b"], ["store-a", "store-b"])
            calls = []

            def first_runner(_root, isolated, store_id):
                calls.append(store_id)
                if store_id == "store-a":
                    status = {"status": "PENDING_REMOTE", "api_write_count": 1, "ozon": {}}
                    result = {"task_id": "task-a", "action": "create", "items": [{"source_sku_id": "sku-a", "offer_id": "offer-a"}]}
                    idempotency = {"payload_hash": "hash-a"}
                else:
                    status = {"status": "NEEDS_ATTENTION", "api_write_count": 0, "error_message": "definite pre-write failure", "ozon": {}}
                    result = {}
                    idempotency = {}
                write(isolated / "status.json", status)
                write(isolated / "output/ozon-result.json", result)
                return {"returncode": 0 if store_id == "store-a" else 1, "status": status, "result": result, "idempotency": idempotency}

            result = execute_selected_stores(root, product, runner=first_runner)
            self.assertEqual(calls, ["store-a", "store-b"])
            self.assertEqual(result["inventory_api_calls"], 0)
            stores = load_publications(product)["stores"]
            self.assertEqual(stores["store-a"]["status"], "PENDING_REMOTE")
            self.assertEqual(stores["store-a"]["sku_publications"][0]["task_id"], "task-a")
            self.assertEqual(stores["store-a"]["sku_publications"][0]["payload_hash"], "hash-a")
            self.assertEqual(stores["store-b"]["status"], "FAILED")
            history = json.loads((product / "status.json").read_text(encoding="utf-8"))["history"]
            self.assertEqual(history[-1]["to"], "NEEDS_ATTENTION")
            handoff_status = json.loads((product / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(handoff_status["status"], "NEEDS_ATTENTION")
            self.assertEqual(handoff_status["progress"], 95)
            upload_steps = [step for step in handoff_status["steps"] if step["name"] == "ozon_upload"]
            self.assertEqual(upload_steps[-1]["status"], "failed")
            self.assertEqual(upload_steps[-1]["error"]["reason"], "definite pre-write failure")

            def recovery_runner(_root, isolated, store_id):
                status = {"status": "UPLOADED", "api_write_count": 1, "ozon": {}}
                result = {
                    "task_id": "task-a", "action": "create",
                    "items": [{"source_sku_id": "sku-a", "offer_id": "offer-a", "task_id": "task-a", "product_id": "product-a"}],
                }
                write(isolated / "status.json", status)
                write(isolated / "output/ozon-result.json", result)
                return {"returncode": 0, "status": status, "result": result, "idempotency": {"payload_hash": "hash-a"}}

            refreshed = refresh_pending_stores(root, product, runner=recovery_runner)
            self.assertEqual(refreshed["write_api_calls"], 0)
            self.assertEqual(refreshed["inventory_api_calls"], 0)
            self.assertEqual(load_publications(product)["stores"]["store-a"]["status"], "SUCCESS")

            retry_calls = []

            def retry_runner(_root, isolated, store_id):
                retry_calls.append(store_id)
                status = {"status": "UPLOADED", "api_write_count": 1, "ozon": {}}
                result = {"task_id": "task-b", "action": "create", "items": [{"source_sku_id": "sku-a", "offer_id": "offer-b", "product_id": "product-b"}]}
                write(isolated / "status.json", status)
                write(isolated / "output/ozon-result.json", result)
                return {"returncode": 0, "status": status, "result": result}

            execute_selected_stores(root, product, only_store_ids=["store-b"], runner=retry_runner)
            self.assertEqual(retry_calls, ["store-b"])
            stores = load_publications(product)["stores"]
            self.assertEqual(stores["store-b"]["status"], "SUCCESS")
            self.assertEqual(stores["store-b"]["sku_publications"][0]["ozon_product_id"], "product-b")
            aggregate = json.loads((product / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(aggregate["ozon"]["upload_status"], "uploaded")
            self.assertEqual(aggregate["ozon"]["offer_id"], "offer-a")
            self.assertEqual(aggregate["ozon"]["product_id"], "product-a")
            self.assertEqual(aggregate["ozon"]["task_id"], "task-a")
            self.assertEqual(aggregate["progress"], 100)
            self.assertNotEqual(aggregate["completed_at"], "unknown")
            execute_selected_stores(root, product, only_store_ids=["store-a"], runner=retry_runner)
            self.assertEqual(retry_calls, ["store-b"])

    def test_zero_write_pending_store_is_not_retryable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = make_product(root)
            data = select_stores(product, ["store-a"], ["store-a"])
            record = data["stores"]["store-a"]
            record.update({"status": "SUBMITTED", "api_write_count": 0, "submission_version": 0})
            record["sku_publications"][0].update({
                "offer_id": "unknown", "task_id": "unknown", "ozon_product_id": "unknown",
            })
            write(product / "output/store-publications.json", data)
            calls = []

            def runner(_root, isolated, store_id):
                calls.append(store_id)
                status = {"status": "SUBMITTED", "api_write_count": 1, "ozon": {}}
                result = {
                    "task_id": "task-a", "action": "create",
                    "items": [{
                        "source_sku_id": "sku-a",
                        "offer_id": json.loads((isolated / "output/ozon-draft.json").read_text(encoding="utf-8"))["skus"][0]["offer_id"],
                        "task_id": "task-a",
                    }],
                }
                write(isolated / "status.json", status)
                write(isolated / "output/ozon-result.json", result)
                return {"returncode": 0, "status": status, "result": result}

            write(product / "output/ozon-draft.json", {
                "offer_id": "draft-offer",
                "skus": [{"source_sku_id": "sku-a", "offer_id": "draft-sku"}],
            })

            result = execute_selected_stores(root, product, runner=runner)

            self.assertEqual(calls, [])
            stores = load_publications(product)["stores"]
            self.assertEqual(stores["store-a"]["status"], "SUBMITTED")
            self.assertEqual(stores["store-a"]["api_write_count"], 0)
            self.assertEqual(stores["store-a"]["sku_publications"][0]["task_id"], "unknown")

    def test_store_specific_cny_price_is_written_only_to_isolated_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = make_product(root)
            select_stores(
                product, ["store-a"], ["store-a"],
                {"store-a": {"sku_prices_cny": {"sku-a": 66}, "sku_prices": {"sku-a": 790}}},
            )
            seen = {}

            def runner(_root, isolated, store_id):
                seen.update(json.loads((isolated / "output/ozon-upload-config.json").read_text(encoding="utf-8")))
                return {"returncode": 1, "status": {"status": "NEEDS_ATTENTION", "api_write_count": 0}, "result": {}}

            execute_selected_stores(root, product, runner=runner)
            self.assertEqual(seen["shop_name"], "store-a")
            self.assertEqual(seen["sku_prices"][0]["price"], "66.00")
            master = json.loads((product / "output/ozon-upload-config.json").read_text(encoding="utf-8"))
            self.assertEqual(master["shop_name"], "default")
            self.assertEqual(master["sku_prices"][0]["price"], "50.00")

    def test_failed_store_reason_is_copied_to_product_ozon_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = make_product(root)
            data = select_stores(product, ["store-a"], ["store-a"])
            record = data["stores"]["store-a"]
            record.update({
                "status": "FAILED",
                "api_write_count": 0,
                "last_error": "图片公网链接无法被真实下载：main-a SSL EOF",
            })
            write(product / "output/store-publications.json", data)

            status = aggregate_product_status(product, data, root)

            self.assertEqual(status["status"], "NEEDS_ATTENTION")
            self.assertEqual(status["ozon"]["upload_status"], "failed")
            self.assertEqual(status["ozon"]["errors"][0]["store_id"], "store-a")
            self.assertIn("SSL EOF", status["ozon"]["errors"][0]["reason"])
            self.assertEqual(status["ozon"]["errors"][0]["api_write_count"], 0)
            self.assertTrue(status["ozon"]["errors"][0]["retryable"])

    def test_ozon_issue_summary_buckets_remote_failures_for_ui_and_retry_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = make_product(root)
            data = select_stores(product, ["store-a"], ["store-a"])
            record = data["stores"]["store-a"]
            record.update({
                "status": "FAILED",
                "api_write_count": 1,
                "last_error": "Ozon returned item validation errors",
            })
            record["sku_publications"][0].update({
                "offer_id": "OFFER-A",
                "task_id": "TASK-A",
                "ozon_product_id": "PRODUCT-A",
                "errors": [{
                    "code": "VALUE_MUST_BE_DECIMAL",
                    "field": "attribute",
                    "level": "ERROR_LEVEL_WARNING",
                    "message": "Value must be decimal",
                }],
            })
            write(product / "output/store-runs/store-a/ozon-result.json", {
                "items": [{
                    "source_sku_id": "sku-a",
                    "offer_id": "OFFER-A",
                    "errors": [{
                        "code": "pics_http_error",
                        "level": "ERROR_LEVEL_WARNING",
                        "message": "photo upload failed",
                    }],
                }],
            })

            summary = summarize_ozon_issues(product, data)
            status = aggregate_product_status(product, data, root)

            self.assertEqual(ozon_issue_bucket({"code": "ML_INCORRECT_VOLUME_WEIGHT"}), "logistics_weight")
            self.assertEqual(summary["primary_bucket"], "numeric_contract")
            self.assertEqual(summary["counts"]["numeric_contract"], 1)
            self.assertEqual(summary["counts"]["image_link"], 1)
            self.assertEqual(status["error_code"], "OZON_NUMERIC_CONTRACT")
            self.assertEqual(status["ozon_issue_summary"]["primary_action"], "repair_attributes")
            self.assertIn("重新编译属性", status["error_message"])

    def test_definitive_deactivated_key_marks_store_connection_failed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product = make_product(root)
            write(root / "ozon-adapter/shops.json", {
                "schema_version": "1.1.0", "default_read_shop": "store-a",
                "shops": [{
                    "id": "store-a", "name": "store-a", "display_name": "Store A",
                    "enabled": True, "validation_status": "connected",
                    "client_id_env": "OZON_STORE_A_CLIENT_ID",
                    "api_key_env": "OZON_STORE_A_API_KEY",
                }],
            })
            (root / "ozon-adapter/.env.store-a").write_text(
                "OZON_STORE_A_CLIENT_ID=1\nOZON_STORE_A_API_KEY=old\n", encoding="utf-8",
            )
            select_stores(product, ["store-a"], ["store-a"])

            def runner(_root, _isolated, _store_id):
                return {
                    "returncode": 1,
                    "status": {
                        "status": "NEEDS_ATTENTION", "api_write_count": 0,
                        "error_message": "Api-key is deactivated, use another one or generate a new one",
                    },
                    "result": {},
                }

            execute_selected_stores(root, product, runner=runner)
            store = list_stores(root)[0]
            self.assertEqual(store["connection_status"], "failed")
            self.assertIn("deactivated", store["last_validation_error"])


if __name__ == "__main__":
    unittest.main()
