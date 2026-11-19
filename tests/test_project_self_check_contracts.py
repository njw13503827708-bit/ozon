import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import project_self_check
from scripts.batch_upload_approval import authorize_batch_upload, build_batch_upload_review
from tests.test_batch_upload_approval import make_ready_product, write_json


def make_batch(root: Path) -> dict:
    make_ready_product(root, "P000101", "C-101", "SKU-101", ["store-a"])
    batch = {
        "batch_id": "B-CHECK-1",
        "status": "AWAITING_BATCH_UPLOAD_CONFIRMATION",
        "products": [{
            "product_id": "P000101",
            "collection_id": "C-101",
            "target_store_ids": ["store-a"],
        }],
    }
    write_json(root / "batches/B-CHECK-1/batch.json", batch)
    return batch


class ProjectSelfCheckContractTests(unittest.TestCase):
    def _product(self, status, source):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        product = Path(temporary.name) / "P000007"
        (product / "input").mkdir(parents=True)
        (product / "status.json").write_text(json.dumps(status), encoding="utf-8")
        (product / "input/source.json").write_text(json.dumps(source), encoding="utf-8")
        return product

    def test_archived_product_is_audit_data(self):
        product = self._product(
            {"status": "ARCHIVED", "archived_at": "2026-07-16T00:00:00+08:00"},
            {},
        )
        self.assertEqual(project_self_check.product_contract_class(product), "archived")

    def test_pre_contract_product_is_not_reported_as_current(self):
        product = self._product({"status": "FAILED"}, {"product_id": "P000007"})
        self.assertEqual(project_self_check.product_contract_class(product), "pre_contract")

    def test_current_contract_requires_matching_workbench_identity(self):
        product = self._product(
            {"status": "WAITING_MANUAL_REVIEW"},
            {
                "product_id": "P000007",
                "collection_id": "C-007",
                "source_kind": "workbench_collection",
            },
        )
        self.assertEqual(project_self_check.product_contract_class(product), "current")

    def test_cross_product_identity_is_pre_contract(self):
        product = self._product(
            {"status": "WAITING_MANUAL_REVIEW"},
            {
                "product_id": "P000008",
                "collection_id": "C-008",
                "source_kind": "workbench_collection",
            },
        )
        self.assertEqual(project_self_check.product_contract_class(product), "pre_contract")

    def test_batch_upload_gate_rejects_preapproval_writes_and_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_batch(root)
            status_path = root / "products/P000101/status.json"
            status = json.loads(status_path.read_text(encoding="utf-8"))
            status["api_write_count"] = 1
            write_json(status_path, status)
            draft_path = root / "products/P000101/output/ozon-draft.json"
            draft = json.loads(draft_path.read_text(encoding="utf-8"))
            draft["inventory"] = {"quantity": 9}
            write_json(draft_path, draft)
            results = []

            with patch.object(project_self_check, "ROOT", root):
                project_self_check.check_batch_upload_gate(results)

            check = next(item for item in results if item["code"] == "batch_upload_gate")
            self.assertFalse(check["ok"])
            self.assertEqual(check["preapproval_write_products"], ["P000101"])
            self.assertIn("inventory_field_forbidden", check["blocker_codes"])

    def test_batch_authorization_integrity_rejects_missing_and_stale_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch = make_batch(root)
            batch["status"] = "UPLOAD_AUTHORIZED"
            write_json(root / "batches/B-CHECK-1/batch.json", batch)
            results = []

            with patch.object(project_self_check, "ROOT", root):
                project_self_check.check_batch_authorization_integrity(results)

            missing = next(item for item in results if item["code"] == "batch_authorization_integrity")
            self.assertFalse(missing["ok"])
            self.assertEqual(missing["missing_authorization_batches"], ["B-CHECK-1"])

            batch["status"] = "AWAITING_BATCH_UPLOAD_CONFIRMATION"
            authorization = authorize_batch_upload(
                root, batch, build_batch_upload_review(root, batch)["review_hash"], "operator-local"
            )
            authorization["review_hash"] = "0" * 64
            write_json(root / "batches/B-CHECK-1/upload-authorization.json", authorization)
            results = []
            with patch.object(project_self_check, "ROOT", root):
                project_self_check.check_batch_authorization_integrity(results)

            stale = next(item for item in results if item["code"] == "batch_authorization_integrity")
            self.assertFalse(stale["ok"])
            self.assertEqual(stale["stale_authorized_batches"], ["B-CHECK-1"])

    def test_postapproval_write_is_not_misreported_as_preapproval(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch = make_batch(root)
            authorize_batch_upload(
                root, batch, build_batch_upload_review(root, batch)["review_hash"], "operator-local"
            )
            status_path = root / "products/P000101/status.json"
            status = json.loads(status_path.read_text(encoding="utf-8"))
            status.update(status="HANDED_OFF_TO_OZON", batch_id="B-CHECK-1", api_write_count=1,
                          ozon={"task_id": "task-1"})
            write_json(status_path, status)
            results = []

            with patch.object(project_self_check, "ROOT", root):
                project_self_check.check_batch_upload_gate(results)
                project_self_check.check_batch_authorization_integrity(results)

            gate = next(item for item in results if item["code"] == "batch_upload_gate")
            integrity = next(item for item in results if item["code"] == "batch_authorization_integrity")
            self.assertTrue(gate["ok"], gate)
            self.assertTrue(integrity["ok"], integrity)

    def test_batch_checks_never_echo_authorization_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch = make_batch(root)
            write_json(root / "batches/B-CHECK-1/upload-authorization.json", {
                "status": "authorized",
                "batch_id": batch["batch_id"],
                "review_hash": "0" * 64,
                "client_secret": "DO-NOT-PRINT-THIS-SECRET",
            })
            results = []

            with patch.object(project_self_check, "ROOT", root):
                project_self_check.check_batch_upload_gate(results)
                project_self_check.check_batch_authorization_integrity(results)

            self.assertNotIn("DO-NOT-PRINT-THIS-SECRET", json.dumps(results, ensure_ascii=False))

    def test_built_edge_runtime_is_accepted_as_current_source_output(self):
        results = []

        project_self_check.check_edge_extension(results)

        check = next(item for item in results if item["code"] == "extension_source_mirror")
        self.assertTrue(check["ok"], check)


if __name__ == "__main__":
    unittest.main()
