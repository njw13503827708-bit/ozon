import importlib.util
import base64
import asyncio
import json
import tempfile
import unittest
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from tests.test_batch_upload_approval import make_ready_product
from scripts.batch_upload_approval import build_batch_upload_review


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "collector/local-ingest/app.py"
SPEC = importlib.util.spec_from_file_location("batch_confirmation_workbench_app", APP_PATH)
workbench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(workbench)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class FakeRequest:
    def __init__(self, payload=None, error: Exception | None = None):
        self.payload = payload
        self.error = error

    async def json(self):
        if self.error:
            raise self.error
        return self.payload


class BatchUploadConfirmationGateTest(unittest.IsolatedAsyncioTestCase):
    def test_invalidation_retains_audit_and_is_idempotent(self):
        from scripts import batch_upload_approval as approval
        self.assertTrue(callable(getattr(approval, "invalidate_batch_upload_authorization", None)))
        record = approval.authorize_batch_upload(self.root, self.batch, self.current_hash(), "operator-7")
        path = approval.authorization_path(self.root, self.batch["batch_id"])
        self.assertTrue(approval.invalidate_batch_upload_authorization(self.root, self.batch["batch_id"], "draft_saved"))
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved["status"], "invalidated")
        self.assertTrue(saved["invalidated_at"].endswith("+00:00"))
        self.assertEqual(saved["invalidation_reason"], "draft_saved")
        for key, value in record.items():
            if key != "status":
                self.assertEqual(saved[key], value)
        approval.invalidate_batch_upload_authorization(self.root, self.batch["batch_id"], "draft_saved")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), saved)
        self.assertFalse(approval.invalidate_batch_upload_authorization(self.root, "B-MISSING", "draft_saved"))
        self.assertFalse(approval.authorization_path(self.root, "B-MISSING").exists())

    async def test_draft_and_sku_override_invalidate_only_after_success(self):
        from scripts.batch_upload_approval import authorize_batch_upload, authorization_path
        for payload in ({"title_ru": "Новое название"}, {"sku_overrides": {"SKU-101": {"selling_price_rub": 1500}}}):
            with self.subTest(payload=payload):
                authorize_batch_upload(self.root, self.batch, self.current_hash(), "operator-7")
                with self.app_patches(lambda *args: {}), patch.object(workbench, "ensure_workbench_product_mutable"), patch.object(workbench, "record_workbench_edits", return_value={}):
                    result = await workbench.save_workbench_draft("P000101", FakeRequest(payload))
                self.assertTrue(result["saved"])
                record = json.loads(authorization_path(self.root, self.batch["batch_id"]).read_text(encoding="utf-8"))
                self.assertEqual(record["status"], "invalidated")

    def test_review_builder_exception_denies_upload(self):
        from scripts import run_batch
        with patch.object(run_batch, "build_batch_upload_review", side_effect=RuntimeError("malformed review")):
            try:
                allowed = run_batch._batch_upload_may_run(self.root, self.batch)
            except Exception:
                self.fail("Review exceptions must return False")
        self.assertFalse(allowed)

    async def test_protected_image_category_and_store_mutations_invalidate(self):
        from scripts.batch_upload_approval import authorize_batch_upload, authorization_path
        for action in ("role", "replace", "delete", "regenerate", "stores", "category"):
            with self.subTest(action=action):
                make_ready_product(self.root, "P000101", "C-101", "SKU-101", ["store-b", "store-a"])
                product = self.root / "products/P000101"
                image = product / "output/generated-images/main.jpg"
                image.parent.mkdir(parents=True, exist_ok=True)
                image.write_bytes(b"generated")
                write_json(product / "output/image-plan.json", {"main_images": [{"slot": "main", "output_path": image.relative_to(self.root).as_posix()}]})
                authorize_batch_upload(self.root, self.batch, self.current_hash(), "operator-7")
                with self.app_patches(lambda *args: {}), patch.object(workbench, "ensure_workbench_product_mutable"), patch.object(workbench, "record_image_feedback", return_value={}), patch.object(workbench, "validate_target_stores", side_effect=lambda value: value), patch.object(workbench, "connected_store_ids", return_value=["store-a", "store-b"]):
                    if action == "role":
                        result = await workbench.update_workbench_image("P000101", "main", FakeRequest({"action": "set_role", "role": "detail"}))
                    elif action == "replace":
                        result = await workbench.replace_workbench_image("P000101", "main", FakeRequest({"data_url": "data:image/jpeg;base64," + base64.b64encode(b"replacement").decode()}))
                    elif action == "delete":
                        result = workbench.delete_workbench_image("P000101", "main")
                    elif action == "regenerate":
                        result = await workbench.queue_single_image_regeneration("P000101", "main", FakeRequest({"prompt": "new prompt"}))
                    elif action == "stores":
                        result = await workbench.save_product_store_selection("P000101", FakeRequest({"store_ids": ["store-a"]}))
                    else:
                        selection = {"category_id": 42, "type_id": 43, "rules_snapshot": {}, "rules_snapshot_hash": "new", "category_path": []}
                        write_json(product / "input/source-manifest.json", {"schema_version": "1.0.0"})
                        with patch.object(workbench, "build_selection", return_value=selection), patch.object(workbench, "validate_json", return_value=[]), patch.object(workbench, "write_source_manifest", return_value={}):
                            result = await workbench.update_collected_product_category("P000101", FakeRequest(selection))
                self.assertTrue(result)
                saved = json.loads(authorization_path(self.root, self.batch["batch_id"]).read_text(encoding="utf-8"))
                self.assertEqual(saved["status"], "invalidated")

    async def test_file_lock_excludes_other_process_and_rejected_edit_preserves_authorization(self):
        from scripts.batch_upload_approval import authorize_batch_upload, authorization_path, batch_upload_lock
        authorize_batch_upload(self.root, self.batch, self.current_hash(), "operator-7")
        path = authorization_path(self.root, self.batch["batch_id"])
        before = path.read_bytes()
        with batch_upload_lock(self.root):
            child = subprocess.run([sys.executable, "-c", "from pathlib import Path; from scripts.batch_upload_approval import batch_upload_lock; import sys\nwith batch_upload_lock(Path(sys.argv[1])): raise AssertionError('acquired')", str(self.root)], cwd=ROOT, capture_output=True, timeout=10)
            self.assertNotEqual(child.returncode, 0)
            self.assertIn(b"BatchUploadBusy", child.stderr)
            with self.app_patches(lambda *args: {}):
                await self.assert_http_error(409, asyncio.create_task(workbench.save_workbench_draft("P000101", FakeRequest({"title_ru": "changed"}))))
        self.assertFalse((self.root / "products/P000101/output/workbench-draft.json").exists())
        with self.app_patches(lambda *args: {}), patch.object(workbench, "ensure_workbench_product_mutable"):
            await self.assert_http_error(422, workbench.save_workbench_draft("P000101", FakeRequest({"forbidden": True})))
        self.assertEqual(path.read_bytes(), before)

    async def test_failed_store_route_reuses_same_authorized_batch_without_payload_rebuild(self):
        from scripts.batch_upload_approval import authorize_batch_upload
        from scripts.store_publications import select_stores, ensure_store_offer_ids, save_publications
        product = self.root / "products/P000101"
        source = json.loads((product / "input/source.json").read_text(encoding="utf-8"))
        source["skus"] = [{"sku_id": "SKU-101"}]
        write_json(product / "input/source.json", source)
        select_stores(product, ["store-a", "store-b"], ["store-a", "store-b"])
        data = ensure_store_offer_ids(product)
        authorize_batch_upload(self.root, self.batch, self.current_hash(), "operator-7")
        for store in data["stores"].values():
            store["status"] = "FAILED"
        save_publications(product, data)
        write_json(product / "status.json", {"product_id": product.name, "batch_id": self.batch["batch_id"], "status": "NEEDS_ATTENTION", "current_step": "ozon_upload", "failed_step": "ozon_upload", "api_write_count": 0})
        before = self.current_hash()
        with self.app_patches(lambda batch, source: {"status": "queued", "batch_id": batch["batch_id"]}), patch.object(workbench, "running_batch_pid", return_value=None), patch.object(workbench, "reserved_product_batches", return_value={}), patch.object(workbench, "connected_store_ids", return_value=["store-a", "store-b"]), patch.object(workbench, "create_batch", side_effect=AssertionError("retry must reuse authorized batch")), patch.object(workbench, "final_snapshot", side_effect=AssertionError("retry must not rebuild payload")):
            first = workbench.retry_failed_store(product.name, "store-a")
            second = await workbench.retry_failed_stores(product.name, FakeRequest({"store_ids": ["store-b"]}))
        self.assertEqual(first["batch_id"], self.batch["batch_id"])
        self.assertEqual(second["batch_id"], self.batch["batch_id"])
        self.assertEqual(self.current_hash(), before)

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        make_ready_product(self.root, "P000102", "C-102", "SKU-102", ["store-a"])
        make_ready_product(self.root, "P000101", "C-101", "SKU-101", ["store-b", "store-a"])
        self.batch = {
            "schema_version": "1.0.0",
            "batch_id": "B-CONFIRM-1",
            "status": "AWAITING_BATCH_UPLOAD_CONFIRMATION",
            "product_count": 2,
            "auto_upload": True,
            "products": [
                {"product_id": "P000102", "collection_id": "C-102", "target_store_ids": ["store-a"]},
                {"product_id": "P000101", "collection_id": "C-101", "target_store_ids": ["store-b", "store-a"]},
            ],
        }
        write_json(self.root / "batches/B-CONFIRM-1/batch.json", self.batch)

    def tearDown(self) -> None:
        self.temp.cleanup()

    @contextmanager
    def app_patches(self, launcher):
        with (
            patch.object(workbench, "ROOT", self.root),
            patch.object(workbench, "PRODUCTS_DIR", self.root / "products"),
            patch.object(workbench, "WORKBENCH_RUN_QUEUE_PATH", self.root / "logs/workbench-run-queue.json"),
            patch.object(workbench, "CURRENT_BATCH_PATH", self.root / "logs/current-batch.json"),
            patch.object(workbench, "BATCH_PID_PATH", self.root / "logs/batch-runner.pid"),
            patch.object(workbench, "current_operator_id", return_value="operator-7"),
            patch.object(workbench, "launch_or_enqueue_batch", side_effect=launcher),
        ):
            yield

    async def assert_http_error(self, status_code: int, awaitable) -> HTTPException:
        with self.assertRaises(workbench.HTTPException) as raised:
            await awaitable
        self.assertEqual(raised.exception.status_code, status_code)
        return raised.exception

    def current_hash(self) -> str:
        return build_batch_upload_review(self.root, self.batch)["review_hash"]

    def audit_events(self) -> list[dict]:
        events = []
        for product_id in ("P000101", "P000102"):
            path = self.root / "products" / product_id / "logs/collector.log.jsonl"
            if path.is_file():
                for line in path.read_text(encoding="utf-8").splitlines():
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return [event for event in events if event.get("event") == "batch_upload_authorized"]

    def test_get_returns_authoritative_read_only_review(self) -> None:
        launched = []
        with self.app_patches(lambda batch, source: launched.append((batch, source))):
            review = workbench.get_workbench_batch_upload_review("B-CONFIRM-1")

        self.assertTrue(review["can_confirm"])
        self.assertEqual(review["product_count"], 2)
        self.assertEqual(review["write_api_calls"], 0)
        self.assertEqual(review["inventory_api_calls"], 0)
        self.assertEqual(launched, [])
        self.assertFalse((self.root / "batches/B-CONFIRM-1/upload-authorization.json").exists())

    async def test_auto_upload_field_means_prepare_then_confirm_not_write_authorization(self) -> None:
        launched = []
        self.assertTrue(self.batch["auto_upload"])
        with self.app_patches(
            lambda batch, source: launched.append((dict(batch), source))
            or {"status": "queued", "batch_id": batch["batch_id"], "queue_position": 1}
        ):
            review = workbench.get_workbench_batch_upload_review(self.batch["batch_id"])
            self.assertEqual([], launched)
            self.assertEqual(0, review["write_api_calls"])
            self.assertFalse(
                (self.root / "batches/B-CONFIRM-1/upload-authorization.json").exists()
            )
            await workbench.confirm_workbench_batch_upload(
                self.batch["batch_id"],
                FakeRequest({"review_hash": review["review_hash"]}),
            )
        self.assertEqual(1, len(launched))

    async def test_successful_post_persists_authorization_and_launches_exact_batch(self) -> None:
        launched = []
        review_hash = self.current_hash()

        with self.app_patches(lambda batch, source: launched.append((dict(batch), source)) or {
            "status": "queued", "batch_id": batch["batch_id"], "queue_position": 1,
        }):
            result = await workbench.confirm_workbench_batch_upload(
                "B-CONFIRM-1", FakeRequest({"review_hash": review_hash})
            )

        saved = json.loads((self.root / "batches/B-CONFIRM-1/upload-authorization.json").read_text(encoding="utf-8"))
        saved_batch = json.loads((self.root / "batches/B-CONFIRM-1/batch.json").read_text(encoding="utf-8"))
        self.assertEqual(saved, result["authorization"])
        self.assertEqual(saved["schema_version"], "1.0.0")
        self.assertEqual(saved["batch_id"], "B-CONFIRM-1")
        self.assertEqual(saved["review_hash"], review_hash)
        self.assertEqual(saved["authorized_by"], "operator-7")
        self.assertTrue(saved["authorized_at"])
        self.assertEqual(saved["product_ids"], ["P000101", "P000102"])
        self.assertEqual(saved["target_store_ids"], ["store-a", "store-b"])
        self.assertEqual(saved["status"], "authorized")
        self.assertEqual(saved_batch["status"], "UPLOAD_AUTHORIZED")
        self.assertEqual(launched[0][0]["batch_id"], "B-CONFIRM-1")
        self.assertEqual([item["product_id"] for item in launched[0][0]["products"]], ["P000102", "P000101"])
        self.assertEqual(launched[0][1], "batch_upload_confirmation")
        self.assertEqual(result["write_api_calls"], 0)
        self.assertEqual(result["inventory_api_calls"], 0)
        events = self.audit_events()
        self.assertEqual(len(events), 2)
        self.assertEqual({event["payload"]["product_id"] for event in events}, {"P000101", "P000102"})
        for event in events:
            self.assertEqual(event["payload"], {
                "batch_id": "B-CONFIRM-1",
                "product_id": event["payload"]["product_id"],
                "product_ids": ["P000101", "P000102"],
                "target_store_ids": ["store-a", "store-b"],
                "review_hash": review_hash,
                "authorized_by": "operator-7",
                "authorized_at": saved["authorized_at"],
            })

    async def test_malformed_hash_or_body_is_422_without_persistence_or_launch(self) -> None:
        cases = [
            FakeRequest(None),
            FakeRequest([]),
            FakeRequest({}),
            FakeRequest({"review_hash": "A" * 64}),
            FakeRequest({"review_hash": "a" * 63}),
            FakeRequest({"review_hash": "a" * 64, "extra": True}),
            FakeRequest(error=ValueError("bad json")),
        ]
        for request in cases:
            with self.subTest(payload=request.payload), self.app_patches(lambda batch, source: self.fail("must not launch")):
                await self.assert_http_error(
                    422, workbench.confirm_workbench_batch_upload("B-CONFIRM-1", request)
                )
                self.assertFalse((self.root / "batches/B-CONFIRM-1/upload-authorization.json").exists())

    async def test_newly_blocked_product_is_409_without_persistence_or_launch(self) -> None:
        review_hash = self.current_hash()
        write_json(self.root / "products/P000101/status.json", {
            "product_id": "P000101", "status": "NEEDS_ATTENTION",
        })

        with self.app_patches(lambda batch, source: self.fail("must not launch")):
            await self.assert_http_error(
                409,
                workbench.confirm_workbench_batch_upload(
                    "B-CONFIRM-1", FakeRequest({"review_hash": review_hash})
                ),
            )
        self.assertFalse((self.root / "batches/B-CONFIRM-1/upload-authorization.json").exists())

    async def test_matching_authorization_does_not_bypass_new_image_qc_blocker(self) -> None:
        review_hash = self.current_hash()
        launched = []
        with self.app_patches(lambda batch, source: launched.append(batch["batch_id"]) or {"status": "queued"}):
            await workbench.confirm_workbench_batch_upload(
                "B-CONFIRM-1", FakeRequest({"review_hash": review_hash})
            )
            qc_path = self.root / "products/P000101/output/image-qc-report.json"
            qc = json.loads(qc_path.read_text(encoding="utf-8"))
            qc.update({"decision": "fail", "critical_failures": [{"code": "blur"}]})
            write_json(qc_path, qc)
            write_json(self.root / "logs/workbench-run-queue.json", {
                "items": [{"batch_id": "B-CONFIRM-1", "source": "batch_upload_confirmation"}],
            })

            await self.assert_http_error(
                409,
                workbench.confirm_workbench_batch_upload(
                    "B-CONFIRM-1", FakeRequest({"review_hash": review_hash})
                ),
            )
        self.assertEqual(launched, ["B-CONFIRM-1"])

    async def test_matching_authorization_does_not_bypass_needs_attention(self) -> None:
        review_hash = self.current_hash()
        launched = []
        with self.app_patches(lambda batch, source: launched.append(batch["batch_id"]) or {"status": "queued"}):
            await workbench.confirm_workbench_batch_upload(
                "B-CONFIRM-1", FakeRequest({"review_hash": review_hash})
            )
            write_json(self.root / "products/P000101/status.json", {
                "product_id": "P000101", "status": "NEEDS_ATTENTION",
                "task_authorized": True, "next_action": "ozon_upload",
                "batch_id": "B-CONFIRM-1", "api_write_count": 0,
            })
            write_json(self.root / "logs/workbench-run-queue.json", {
                "items": [{"batch_id": "B-CONFIRM-1", "source": "batch_upload_confirmation"}],
            })

            await self.assert_http_error(
                409,
                workbench.confirm_workbench_batch_upload(
                    "B-CONFIRM-1", FakeRequest({"review_hash": review_hash})
                ),
            )
        self.assertEqual(launched, ["B-CONFIRM-1"])

    async def test_missing_or_inaccessible_batch_is_404(self) -> None:
        review_hash = self.current_hash()
        with self.app_patches(lambda batch, source: self.fail("must not launch")):
            await self.assert_http_error(
                404,
                workbench.confirm_workbench_batch_upload(
                    "B-MISSING", FakeRequest({"review_hash": review_hash})
                ),
            )
            with patch.object(
                workbench, "require_owned_batch",
                side_effect=workbench.HTTPException(status_code=404, detail="任务不存在"),
            ):
                await self.assert_http_error(
                    404,
                    workbench.confirm_workbench_batch_upload(
                        "B-CONFIRM-1", FakeRequest({"review_hash": review_hash})
                    ),
                )

    async def test_cross_batch_authorization_record_is_rejected_and_preserved(self) -> None:
        existing = {
            "schema_version": "1.0.0", "batch_id": "B-OTHER",
            "review_hash": self.current_hash(), "authorized_at": "2026-01-01T00:00:00+00:00",
            "authorized_by": "operator-x", "product_ids": ["P000101", "P000102"],
            "target_store_ids": ["store-a", "store-b"], "status": "authorized",
        }
        path = self.root / "batches/B-CONFIRM-1/upload-authorization.json"
        write_json(path, existing)

        with self.app_patches(lambda batch, source: self.fail("must not launch")):
            await self.assert_http_error(
                409,
                workbench.confirm_workbench_batch_upload(
                    "B-CONFIRM-1", FakeRequest({"review_hash": existing["review_hash"]})
                ),
            )
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), existing)

    async def test_repeated_matching_confirmation_returns_same_record_and_launches_once(self) -> None:
        launched = []
        review_hash = self.current_hash()
        launcher = lambda batch, source: launched.append((batch["batch_id"], source)) or {"status": "started"}
        with self.app_patches(launcher):
            first = await workbench.confirm_workbench_batch_upload(
                "B-CONFIRM-1", FakeRequest({"review_hash": review_hash})
            )
            second = None
            for batch_status, product_status, api_writes in (
                ("QUEUED", "QUEUED", 0),
                ("RUNNING", "PROCESSING", 0),
                ("SUBMITTED", "HANDED_OFF_TO_OZON", 1),
            ):
                current_batch = json.loads(
                    (self.root / "batches/B-CONFIRM-1/batch.json").read_text(encoding="utf-8")
                )
                current_batch["status"] = batch_status
                write_json(self.root / "batches/B-CONFIRM-1/batch.json", current_batch)
                write_json(self.root / "products/P000101/status.json", {
                    "product_id": "P000101", "status": product_status,
                    "task_authorized": True, "next_action": "ozon_upload",
                    "batch_id": "B-CONFIRM-1", "api_write_count": api_writes,
                })
                if batch_status == "QUEUED":
                    write_json(self.root / "logs/workbench-run-queue.json", {
                        "items": [{"batch_id": "B-CONFIRM-1", "source": "batch_upload_confirmation"}],
                    })
                else:
                    (self.root / "logs/workbench-run-queue.json").unlink(missing_ok=True)
                second = await workbench.confirm_workbench_batch_upload(
                    "B-CONFIRM-1", FakeRequest({"review_hash": review_hash})
                )

        self.assertEqual(first["authorization"], second["authorization"])
        self.assertEqual(len(launched), 1)
        self.assertTrue(second["replayed"])
        self.assertEqual(len(self.audit_events()), 2)

    async def test_batch_status_write_failure_is_recovered_without_duplicate_dispatch(self) -> None:
        review_hash = self.current_hash()
        launched = []

        with self.app_patches(lambda batch, source: launched.append(batch["batch_id"]) or {"status": "queued"}):
            with patch.object(workbench, "atomic_write_json", side_effect=OSError("batch write failed")):
                with self.assertRaises(OSError):
                    await workbench.confirm_workbench_batch_upload(
                        "B-CONFIRM-1", FakeRequest({"review_hash": review_hash})
                    )
            self.assertTrue((self.root / "batches/B-CONFIRM-1/upload-authorization.json").is_file())
            self.assertEqual(launched, [])

            def persist_queue(batch, source):
                launched.append(batch["batch_id"])
                write_json(self.root / "logs/workbench-run-queue.json", {
                    "items": [{"batch_id": batch["batch_id"], "source": source}],
                })
                return {"status": "queued"}

            with patch.object(workbench, "launch_or_enqueue_batch", side_effect=persist_queue):
                recovered = await workbench.confirm_workbench_batch_upload(
                    "B-CONFIRM-1", FakeRequest({"review_hash": review_hash})
                )
                replay = await workbench.confirm_workbench_batch_upload(
                    "B-CONFIRM-1", FakeRequest({"review_hash": review_hash})
                )

        self.assertFalse(recovered["replayed"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(launched, ["B-CONFIRM-1"])
        self.assertEqual(len(self.audit_events()), 2)

    async def test_launcher_failure_is_retried_once_then_durable_queue_suppresses_replay(self) -> None:
        review_hash = self.current_hash()
        attempts = []

        def failing_launcher(batch, source):
            attempts.append("failed")
            raise OSError("launcher failed before durable dispatch")

        with self.app_patches(failing_launcher):
            with self.assertRaises(OSError):
                await workbench.confirm_workbench_batch_upload(
                    "B-CONFIRM-1", FakeRequest({"review_hash": review_hash})
                )

            def persist_queue(batch, source):
                attempts.append("queued")
                write_json(self.root / "logs/workbench-run-queue.json", {
                    "items": [{"batch_id": batch["batch_id"], "source": source}],
                })
                return {"status": "queued"}

            with patch.object(workbench, "launch_or_enqueue_batch", side_effect=persist_queue):
                recovered = await workbench.confirm_workbench_batch_upload(
                    "B-CONFIRM-1", FakeRequest({"review_hash": review_hash})
                )
                replay = await workbench.confirm_workbench_batch_upload(
                    "B-CONFIRM-1", FakeRequest({"review_hash": review_hash})
                )

        self.assertFalse(recovered["replayed"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(attempts, ["failed", "queued"])
        self.assertEqual(len(self.audit_events()), 2)

    async def test_truncated_audit_tail_is_preserved_and_separated_before_idempotent_event(self) -> None:
        review_hash = self.current_hash()
        truncated = b'{"at":"older","event":"interrupted"'
        for product_id in ("P000101", "P000102"):
            log_path = self.root / "products" / product_id / "logs/collector.log.jsonl"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_bytes(truncated)

        def persist_queue(batch, source):
            write_json(self.root / "logs/workbench-run-queue.json", {
                "items": [{"batch_id": batch["batch_id"], "source": source}],
            })
            return {"status": "queued"}

        with self.app_patches(persist_queue):
            await workbench.confirm_workbench_batch_upload(
                "B-CONFIRM-1", FakeRequest({"review_hash": review_hash})
            )
            first_bytes = {
                product_id: (self.root / "products" / product_id / "logs/collector.log.jsonl").read_bytes()
                for product_id in ("P000101", "P000102")
            }
            await workbench.confirm_workbench_batch_upload(
                "B-CONFIRM-1", FakeRequest({"review_hash": review_hash})
            )

        self.assertEqual(len(self.audit_events()), 2)
        for product_id, before_replay in first_bytes.items():
            log_path = self.root / "products" / product_id / "logs/collector.log.jsonl"
            self.assertTrue(before_replay.startswith(truncated + b"\n"))
            self.assertEqual(log_path.read_bytes(), before_replay)

    async def test_draft_changed_after_get_rejects_stale_hash(self) -> None:
        review_hash = self.current_hash()
        draft_path = self.root / "products/P000101/output/ozon-draft.json"
        draft = json.loads(draft_path.read_text(encoding="utf-8"))
        draft["title_ru"] = "Изменено после проверки"
        write_json(draft_path, draft)

        with self.app_patches(lambda batch, source: self.fail("must not launch")):
            await self.assert_http_error(
                409,
                workbench.confirm_workbench_batch_upload(
                    "B-CONFIRM-1", FakeRequest({"review_hash": review_hash})
                ),
            )
        self.assertFalse((self.root / "batches/B-CONFIRM-1/upload-authorization.json").exists())

    async def test_image_bytes_changed_after_get_rejects_stale_hash(self) -> None:
        review_hash = self.current_hash()
        (self.root / "products/P000101/output/accepted-images/SKU-101.jpg").write_bytes(b"changed")

        with self.app_patches(lambda batch, source: self.fail("must not launch")):
            await self.assert_http_error(
                409,
                workbench.confirm_workbench_batch_upload(
                    "B-CONFIRM-1", FakeRequest({"review_hash": review_hash})
                ),
            )
        self.assertFalse((self.root / "batches/B-CONFIRM-1/upload-authorization.json").exists())

    async def test_empty_membership_never_authorizes(self) -> None:
        empty = {**self.batch, "product_count": 0, "products": []}
        write_json(self.root / "batches/B-CONFIRM-1/batch.json", empty)
        review_hash = build_batch_upload_review(self.root, empty)["review_hash"]

        with self.app_patches(lambda batch, source: self.fail("must not launch")):
            await self.assert_http_error(
                409,
                workbench.confirm_workbench_batch_upload(
                    "B-CONFIRM-1", FakeRequest({"review_hash": review_hash})
                ),
            )
        self.assertFalse((self.root / "batches/B-CONFIRM-1/upload-authorization.json").exists())

    async def test_invalid_batch_identifier_is_422_before_path_access(self) -> None:
        with self.app_patches(lambda batch, source: self.fail("must not launch")):
            await self.assert_http_error(
                422,
                workbench.confirm_workbench_batch_upload(
                    "../outside", FakeRequest({"review_hash": "a" * 64})
                ),
            )


if __name__ == "__main__":
    unittest.main()
