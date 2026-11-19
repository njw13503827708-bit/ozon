import base64
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from scripts.production_input_guard import write_source_manifest
from scripts.openai_images2 import generate_slot


class OpenAIImages2Test(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.product = Path(self.temp.name) / "products/PAPI"
        source = self.product / "input/main-images/source.png"
        source.parent.mkdir(parents=True)
        Image.new("RGB", (960, 1280)).save(source)
        (self.product / "input/source.json").write_text(json.dumps({"product_id":"PAPI", "collection_id":"COLLECT-A", "source_kind":"workbench_collection"}), encoding="utf-8")
        write_source_manifest(self.product)
        self.slot = {"slot": "main-001", "prompt": "Preserve the actual product.",
                     "reference_images": ["products/PAPI/input/main-images/source.png"],
                     "output_path": "products/PAPI/output/generated-images/variant-main/main-001.png"}
        self.bytes = source.read_bytes()
        (self.product / "output").mkdir()
        self.write_plan()

    def write_plan(self):
        (self.product / "output/image-plan.json").write_text(json.dumps({
            "main_images": [self.slot], "generator_contract": {"true_parallel_slot_executor": True}
        }), encoding="utf-8")

    def response(self):
        value = io.BytesIO(json.dumps({"data": [{"b64_json": base64.b64encode(self.bytes).decode()}],
                                      "usage": {"total_tokens": 1}}).encode())
        value.headers = {"x-request-id": "req-fixture"}
        return value

    def test_fixed_model_reference_edit_and_candidate_only(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "fake-secret"}), patch("scripts.openai_images2.urllib.request.OpenerDirector.open", return_value=self.response()) as request:
            proof = generate_slot(self.product, self.slot)
        body = json.loads(request.call_args.args[0].data)
        self.assertEqual("https://api.openai.com/v1/images/edits", request.call_args.args[0].full_url)
        self.assertEqual("gpt-image-2", body["model"])
        self.assertEqual("960x1280", body["size"])
        self.assertEqual(1, body["n"])
        self.assertNotIn("input_fidelity", body)
        self.assertTrue(body["images"][0]["image_url"].startswith("data:image/png;base64,"))
        self.assertEqual("NEEDS_REVIEW", proof["status"])
        self.assertEqual("req-fixture", proof["request_id"])
        self.assertNotIn("fake-secret", json.dumps(proof))
        self.assertFalse((self.product / "output/image-hard-gate.json").exists())
        self.assertFalse((self.product / "output/image-slot-results/main-001.json").exists())

    def test_missing_key_and_invalid_paths_never_call_api(self):
        with patch.dict(os.environ, {}, clear=True), patch("scripts.openai_images2.urllib.request.OpenerDirector.open") as request:
            with self.assertRaisesRegex(ValueError, "OPENAI_API_KEY"):
                generate_slot(self.product, self.slot)
            request.assert_not_called()
        with patch.dict(os.environ, {"OPENAI_API_KEY": "fake-secret"}), patch("scripts.openai_images2.urllib.request.OpenerDirector.open") as request:
            for values in [{"reference_images": []}, {"reference_images": ["https://example.test/a.png"]},
                           {"output_path": "products/PAPI/input/main-images/source.png"}]:
                with self.assertRaises(ValueError):
                    generate_slot(self.product, {**self.slot, **values})
            request.assert_not_called()

    def test_http_error_is_redacted_and_not_retried(self):
        error = urllib.error.HTTPError("https://api.openai.com/v1/images/edits", 401, "fake-secret", {}, io.BytesIO(b"fake-secret"))
        with patch.dict(os.environ, {"OPENAI_API_KEY": "fake-secret"}), patch("scripts.openai_images2.urllib.request.OpenerDirector.open", side_effect=error) as request:
            with self.assertRaisesRegex(RuntimeError, "HTTP 401") as captured:
                generate_slot(self.product, self.slot)
            self.assertNotIn("fake-secret", str(captured.exception))
            self.assertEqual(1, request.call_count)

    def test_candidate_checkpoint_is_reused_without_a_second_paid_request(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "fake-secret"}), patch("scripts.openai_images2.urllib.request.OpenerDirector.open", return_value=self.response()) as request:
            first = generate_slot(self.product, self.slot)
            second = generate_slot(self.product, self.slot)
            self.assertEqual(first, second)
            self.assertEqual(1, request.call_count)

    def test_unknown_image_request_is_not_replayed_after_restart(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY':'fake-secret'}), patch('scripts.openai_images2.urllib.request.OpenerDirector.open', side_effect=TimeoutError()) as request:
            for _ in range(2):
                with self.assertRaises(RuntimeError): generate_slot(self.product, self.slot)
            self.assertEqual(1, request.call_count)

    def test_backend_gateway_configuration_is_used_for_image_edits(self):
        from backend.integrations.ai.provider_config import configure
        root = self.product.parent.parent
        with patch.dict(os.environ, {}, clear=True):
            configure(root, {'base_url':'https://ai.opendoor.sbs','model':'configured-model','api_key':'fake-secret'})
            with patch('scripts.openai_images2.urllib.request.OpenerDirector.open', return_value=self.response()) as request:
                generate_slot(self.product, self.slot)
                self.assertEqual('https://ai.opendoor.sbs/v1/images/edits', request.call_args.args[0].full_url)

    def test_pipeline_generates_candidate_then_keeps_visual_qc(self):
        from scripts import run_batch
        with patch("scripts.openai_images2.generate_slot", return_value={"model": "gpt-image-2"}) as generate, patch.object(run_batch, "codex_exec_command", return_value=["fake"]), patch.object(run_batch, "run_registered_process"), patch.object(run_batch, "validate_image_slot_result", return_value={"status": "failed", "error": "needs review"}), patch.object(run_batch, "codex_worker_unavailable", return_value=False):
            result = run_batch.run_single_image_slot(self.product, {"image_generation_provider": "openai_images2"}, self.slot, 1)
            generate.assert_called_once_with(self.product, self.slot)
            self.assertEqual("failed", result["status"])

    def test_source_gate_requires_the_requested_model(self):
        from scripts.image_slot_scheduler import has_builtin_image_source
        receipt = {"generation_source": "openai_images2", "model": "gpt-image-2",
                   "designer_prompt_followed": True, "local_script_generation": False}
        self.assertTrue(has_builtin_image_source(receipt))
        self.assertFalse(has_builtin_image_source({**receipt, "model": "gpt-image-2.5-flare"}))

    def test_real_wave_preserves_prompt_and_references(self):
        from scripts import run_batch
        from scripts.image_wave_executor import execute_image_slot_waves
        with patch.dict(os.environ, {"OPENAI_API_KEY": "fake-secret"}), patch("scripts.openai_images2.urllib.request.OpenerDirector.open", return_value=self.response()) as request, patch.object(run_batch, "codex_exec_command", return_value=["fake"]), patch.object(run_batch, "run_registered_process"), patch.object(run_batch, "validate_image_slot_result", return_value={"status": "failed", "error": "needs review"}), patch.object(run_batch, "codex_worker_unavailable", return_value=False):
            result = execute_image_slot_waves(self.product, 1, lambda slot, attempt: run_batch.run_single_image_slot(self.product, {"image_generation_provider": "openai_images2"}, slot, attempt), max_attempts=1)
        self.assertFalse(result["prelaunch_failure"])
        self.assertEqual(1, request.call_count)
        body = json.loads(request.call_args.args[0].data)
        self.assertIn(self.slot["prompt"], body["prompt"])
        self.assertEqual(1, len(body["images"]))
        self.assertEqual(1, len(result["failed"]))

    def test_changed_plan_or_reference_invalidates_api_checkpoint(self):
        from scripts.image_slot_scheduler import generation_source_matches_output
        with patch.dict(os.environ, {"OPENAI_API_KEY": "fake-secret"}), patch("scripts.openai_images2.urllib.request.OpenerDirector.open", return_value=self.response()):
            proof = generate_slot(self.product, self.slot)
        output = self.product / "output/generated-images/variant-main/main-001.png"
        receipt = {**proof, "designer_prompt_followed": True, "local_script_generation": False}
        self.assertTrue(generation_source_matches_output(receipt, output))
        self.slot["prompt"] = "A changed composition"
        self.write_plan()
        self.assertFalse(generation_source_matches_output(receipt, output))
        self.slot["prompt"] = "Preserve the actual product."
        self.write_plan()
        Image.new("RGB", (960, 1280), "red").save(self.product / "input/main-images/source.png")
        write_source_manifest(self.product)
        self.assertFalse(generation_source_matches_output(receipt, output))

    def test_api_source_requires_matching_saved_generation_proof(self):
        from scripts.image_slot_scheduler import generation_source_matches_output
        with patch.dict(os.environ, {"OPENAI_API_KEY": "fake-secret"}), patch("scripts.openai_images2.urllib.request.OpenerDirector.open", return_value=self.response()):
            proof = generate_slot(self.product, self.slot)
        output = self.product / "output/generated-images/variant-main/main-001.png"
        receipt = {**proof, "designer_prompt_followed": True, "local_script_generation": False}
        self.assertTrue(generation_source_matches_output(receipt, output))
        output.with_suffix(".generation.json").unlink()
        self.assertFalse(generation_source_matches_output(receipt, output))

    def test_legacy_pixel_lock_cannot_bypass_current_api_inputs(self):
        from scripts.image_slot_scheduler import pending_slots
        with patch.dict(os.environ, {"OPENAI_API_KEY": "fake-secret"}), patch("scripts.openai_images2.urllib.request.OpenerDirector.open", return_value=self.response()):
            proof = generate_slot(self.product, self.slot)
        lock = self.product / "output/product-lock/main-001.json"
        lock.parent.mkdir()
        lock.write_text(json.dumps({"audit": {"status": "PASS"}, "sha256": proof["sha256"]}), encoding="utf-8")
        self.slot["prompt"] = "Changed prompt"
        plan = {"main_images": [self.slot], "generator_contract": {
            "true_parallel_slot_executor": True, "product_pixel_lock_required": True}}
        (self.product / "output/image-plan.json").write_text(json.dumps(plan), encoding="utf-8")
        self.assertEqual(1, pending_slots(self.product, 1)["pending_slot_count"])
        (self.product / "output/image-regeneration-request.json").write_text(json.dumps({"requested_slots": ["main-001"]}), encoding="utf-8")
        self.assertEqual(1, pending_slots(self.product, 1)["pending_slot_count"])

    def test_invalid_image_response_does_not_overwrite_source_or_save_candidate(self):
        response = io.BytesIO(b'{"data":[{"b64_json":"not-an-image"}]}')
        response.headers = {}
        with patch.dict(os.environ, {"OPENAI_API_KEY": "fake-secret"}), patch("scripts.openai_images2.urllib.request.OpenerDirector.open", return_value=response):
            with self.assertRaises(RuntimeError):
                generate_slot(self.product, self.slot)
        self.assertEqual(self.bytes, (self.product / "input/main-images/source.png").read_bytes())
        self.assertFalse((self.product / "output/generated-images/variant-main/main-001.png").exists())

    def test_self_check_only_allows_authorized_client_and_still_checks_secrets(self):
        from scripts import project_self_check
        root = Path(self.temp.name) / "code"
        scripts = root / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "openai_images2.py").write_text('url="https://api.openai.com/v1/images/edits"\nkey="OPENAI_API_KEY"', encoding="utf-8")
        (scripts / "run_batch.py").write_text('key="OPENAI_API_KEY"', encoding="utf-8")
        with patch.object(project_self_check, "ROOT", root):
            results = []
            project_self_check.check_secrets_and_forbidden_clients(results)
            self.assertTrue(next(item for item in results if item["code"] == "external_ai_clients")["ok"])
            (scripts / "other.py").write_text('url="https://api.openai.com/v1/images/edits"', encoding="utf-8")
            with (scripts / "openai_images2.py").open("a", encoding="utf-8") as handle:
                handle.write('\napi_' + 'key="' + ('x' * 24) + '"')
            results = []
            project_self_check.check_secrets_and_forbidden_clients(results)
            self.assertFalse(next(item for item in results if item["code"] == "external_ai_clients")["ok"])
            self.assertFalse(next(item for item in results if item["code"] == "hardcoded_credentials")["ok"])


if __name__ == "__main__":
    unittest.main()
