import copy
import json
import os
import unittest
from unittest.mock import patch


def model_output():
    return {"strategy": {"summary": "Уточнить текст по исходным данным", "reasons": []},
            "content": {"title_ru": "Органайзер для ванной", "description_ru": "Органайзер для ванной.",
                        "tags": ["#органайзер"], "rich_content": {"modules": []}},
            "image_plan": [], "unresolved_facts": []}


def response(value=None):
    return {"id": "resp_test", "model": "configured-model", "status": "completed", "usage": {"input_tokens": 10, "output_tokens": 20},
            "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(value or model_output())}]}]}


class AIContentTest(unittest.TestCase):
    def setUp(self):
        from backend.integrations.ai import openai_content
        self.client = openai_content
        self.context = {"source": {"store_id": "a", "listing_id": 1, "title_ru": "Органайзер для ванной"}}
        self.env = patch.dict(os.environ, {"OPENAI_API_KEY": "test-secret-key", "OPENAI_TEXT_MODEL": "configured-model"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_missing_key_sends_zero_requests(self):
        calls = []
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            with self.assertRaisesRegex(ValueError, "OPENAI_API_KEY"):
                self.client.generate_content(self.context, model="configured-model", transport=lambda *args: calls.append(args))
        self.assertEqual([], calls)

    def test_valid_output_records_usage_and_preserves_target(self):
        before = copy.deepcopy(self.context)
        calls = []
        value = self.client.generate_content(self.context, model="configured-model", transport=lambda *args: calls.append(args) or response())
        self.assertEqual(before, self.context)
        self.assertEqual(1, len(calls))
        self.assertEqual("configured-model", calls[0][1]["model"])
        self.assertEqual(20, value["provider_evidence"]["usage"]["output_tokens"])
        self.assertNotIn("test-secret-key", json.dumps(value))

    def test_invalid_model_output_rejected(self):
        value = model_output(); value["content"]["tags"] = "wrong type"
        with self.assertRaises(ValueError):
            self.client.generate_content(self.context, model="configured-model", transport=lambda *args: response(value))

    def test_model_cannot_replace_target_identity(self):
        value = model_output(); value["store_id"] = "foreign"
        with self.assertRaises(ValueError):
            self.client.generate_content(self.context, model="configured-model", transport=lambda *args: response(value))

    def test_timeout_has_no_automatic_paid_retry(self):
        calls = []
        def send(*args):
            calls.append(args)
            raise TimeoutError("test-secret-key")
        with self.assertRaisesRegex(RuntimeError, "结果未知"):
            self.client.generate_content(self.context, model="configured-model", transport=send)
        self.assertEqual(1, len(calls))

    def test_error_response_redacts_key(self):
        def send(*args):
            raise OSError("authorization test-secret-key failed")
        with self.assertRaises(RuntimeError) as error:
            self.client.generate_content(self.context, model="configured-model", transport=send)
        self.assertNotIn("test-secret-key", str(error.exception))


if __name__ == "__main__":
    unittest.main()
