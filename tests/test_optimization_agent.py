import os
import unittest
from unittest.mock import patch

from backend.repositories.local_operations import LocalOperationsRepository
from tests import test_product_optimization as fixtures
from tests.test_ai_content_client import model_output


class OptimizationAgentTest(unittest.TestCase):
    def setUp(self):
        fixtures.ProductOptimizationTest.setUp(self)
        self.repo = LocalOperationsRepository(self.root)
        self.repo.initialize()
        from backend.services.optimization_agent import OptimizationAgent
        self.agent = OptimizationAgent(self.root)
        self.source = self.service.source("a", self.listing)
        self.env = patch.dict(os.environ, {"OPENAI_API_KEY":"test-only", "OPENAI_TEXT_MODEL":"configured-model"})
        self.env.start(); self.addCleanup(self.env.stop)

    def start(self):
        return self.agent.start("a", self.listing, 0, self.source["source_revision"],
                                ["title_ru", "description_ru", "tags", "rich_content"],
                                {"date_from":"2026-09-26", "date_to":"2026-10-02"}, "host")

    def generated(self, *_args, **_kwargs):
        value = model_output(); value["provider_evidence"] = {"response_id":"resp_test", "usage":{"output_tokens":10}}
        return value

    def test_duplicate_start_reuses_frozen_task_and_completed_result(self):
        first = self.start(); self.assertEqual(first["id"], self.start()["id"])
        with patch("backend.services.optimization_agent.generate_content", side_effect=self.generated) as provider:
            result = self.agent.run(first["id"])
            self.assertEqual("REVIEW_REQUIRED", result["status"])
            self.agent.resume(first["id"])
            self.assertEqual(1, provider.call_count)
        self.assertEqual(first["id"], self.start()["id"])
        self.assertEqual(1, len(self.repo.versions("a", self.listing)))

    def test_title_only_preserves_source_tags_and_opaque_rich_content_scope(self):
        self.erp.persist_listing_attributes('a',[{'offer_id':'item-a','attributes':[{'id':23171,'values':[{'value':'#ванная'}]},
            {'id':11254,'values':[{'value':'{"content":[{"widgetName":"existing"}]}'}]}]}])
        source=self.service.source('a',self.listing)
        job=self.agent.start('a',self.listing,0,source['source_revision'],['title_ru'],
            {'date_from':'2026-09-26','date_to':'2026-10-02'},'host')
        with patch('backend.services.optimization_agent.generate_content',side_effect=self.generated):self.agent.run(job['id'])
        content=self.repo.versions('a',self.listing)[0]['content']
        self.assertEqual(['#ванная'],content['tags'])
        self.assertEqual(['title_ru'],content['changed_fields'])

    def test_unknown_paid_result_does_not_regenerate(self):
        from backend.integrations.ai.openai_content import ProviderResultUnknown
        job = self.start()
        with patch("backend.services.optimization_agent.generate_content", side_effect=ProviderResultUnknown("结果未知")) as provider:
            self.assertEqual("RESULT_UNKNOWN", self.agent.run(job["id"])["status"])
            self.assertEqual("RESULT_UNKNOWN", self.agent.resume(job["id"])["status"])
            self.assertEqual(1, provider.call_count)

    def test_restart_during_dispatch_does_not_repeat_paid_step(self):
        from scripts.pipeline_runtime import write_json_atomic
        job = self.start()
        write_json_atomic(self.agent.task_dir(job["id"]) / "state.json", {"stage":"GENERATE", "effect":"DISPATCHING"})
        with patch("backend.services.optimization_agent.generate_content") as provider:
            self.assertEqual("RESULT_UNKNOWN", self.agent.resume(job["id"])["status"])
            provider.assert_not_called()

    def test_restart_reuses_completed_provider_output_with_abandoned_lock(self):
        from scripts.pipeline_runtime import write_json_atomic
        job = self.start(); directory = self.agent.task_dir(job['id'])
        write_json_atomic(directory/'result.json', self.generated())
        write_json_atomic(directory/'state.json', {'stage':'VALIDATE','effect':'COMPLETED'})
        (directory/'run.lock').write_text('abandoned process', encoding='utf-8')
        self.repo.update_job(job['id'], status='RUNNING')
        with patch('backend.services.optimization_agent.generate_content') as provider:
            self.assertEqual('REVIEW_REQUIRED', self.agent.resume(job['id'])['status'])
            provider.assert_not_called()

    def test_cancel_stops_unsent_steps_and_foreign_task_refused(self):
        job = self.start()
        with self.assertRaises(PermissionError): self.agent.view("b", self.listing, job["id"])
        with self.assertRaises(PermissionError): self.agent.view("a", self.listing+100, job["id"])
        self.agent.cancel(job["id"])
        with patch("backend.services.optimization_agent.generate_content") as provider:
            self.assertEqual("CANCELLED", self.agent.run(job["id"])["status"])
            provider.assert_not_called()

    def test_source_changed_keeps_generation_but_does_not_overwrite_draft(self):
        job = self.start()
        def provider(*args, **kwargs):
            self.erp.upsert_product({"id":"real-a", "description_ru":"Источник изменён"})
            return self.generated()
        with patch("backend.services.optimization_agent.generate_content", side_effect=provider):
            result = self.agent.run(job["id"])
        self.assertEqual("SOURCE_CHANGED", result["status"])
        self.assertTrue(result["result"]["content"])
        self.assertEqual([], self.repo.versions("a", self.listing))

    def test_concurrent_saved_edit_keeps_both_outputs(self):
        job = self.start()
        def provider(*args, **kwargs):
            self.repo.save_version("a", self.listing, {"title_ru":"Ручное изменение"}, self.source["source_revision"], 0, "human")
            return self.generated()
        with patch("backend.services.optimization_agent.generate_content", side_effect=provider):
            result = self.agent.run(job["id"])
        self.assertEqual("VERSION_CHANGED", result["status"])
        self.assertEqual("Ручное изменение", self.repo.versions("a", self.listing)[0]["content"]["title_ru"])
        self.assertTrue(result["result"]["content"])


if __name__ == "__main__": unittest.main()
