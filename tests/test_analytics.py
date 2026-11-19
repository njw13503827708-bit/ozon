from backend.db.erp import initialize as initialize_erp_test_database
import tempfile
import unittest
from unittest.mock import patch
from decimal import Decimal
from pathlib import Path

from backend.integrations.ozon.analytics_client import OzonAnalyticsClient
from backend.repositories.analytics import AnalyticsRepository
from backend.repositories.erp import ErpRepository
from backend.services.analytics_service import AnalyticsService, abc_analysis, funnel


class AnalyticsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        initialize_erp_test_database(self.root)
        self.erp = ErpRepository(self.root)
        self.erp.upsert_store({"id": "a", "currency": "RUB", "platform": "OZON"})
        self.erp.upsert_store({"id": "b", "currency": "CNY", "platform": "OZON"})
        self.repo = AnalyticsRepository(self.root)

    def test_missing_coverage_lists_only_complete_owned_snapshots(self):
        self.repo.save("a", "2026-09-26", "2026-10-02", [], {"revenue": "AVAILABLE"})
        self.repo.save("b", "2026-09-01", "2026-09-03", [], {"revenue": "AVAILABLE"})
        result = AnalyticsService(self.repo, None).read("a", "2026-10-03", "2026-10-04")
        self.assertEqual("NOT_SYNCED", result["availability"])
        self.assertEqual("2026-10-03", result["date_from"])
        self.assertIsNone(result["totals"]["revenue"])
        self.assertEqual([("2026-09-26", "2026-10-02")], [(s["date_from"], s["date_to"]) for s in result["available_snapshots"]])

    def test_rate_limit_is_shared_by_account_across_clients(self):
        clock = [0.0]
        sleeps = []
        def sleep(seconds):
            sleeps.append(seconds)
            clock[0] += seconds
        first = OzonAnalyticsClient("rate-test", "fake-key", sleep=sleep)
        second = OzonAnalyticsClient("rate-test", "fake-key", sleep=sleep)
        from backend.integrations.ozon.base_client import OzonBaseClient
        with patch.object(OzonAnalyticsClient, "_accounts", {}), patch("backend.integrations.ozon.analytics_client.time.monotonic", side_effect=lambda: clock[0]), patch.object(OzonBaseClient, "_request_once", return_value={"result": {"data": []}}) as request:
            first.get_data("2026-10-01", "2026-10-02", ["revenue"])
            second.get_data("2026-10-01", "2026-10-02", ["revenue"])
            self.assertEqual([60.0], sleeps)
            self.assertEqual(2, request.call_count)

    def test_cancel_during_last_request_does_not_save_snapshot(self):
        job = self.erp.create_sync_job("OZON_ANALYTICS_SYNC", "a")
        def transport(*_):
            self.erp.request_sync_job_cancel(job)
            return {"result": {"data": []}}
        with self.assertRaises(InterruptedError):
            AnalyticsService(self.repo, OzonAnalyticsClient("fake-client", "fake-key", transport=transport)).refresh("a", "2026-10-01", "2026-10-02", job_id=job)
        self.assertIsNone(self.repo.latest("a", "2026-10-01", "2026-10-02"))

    def test_fixed_read_contract_and_validation(self):
        calls = []
        client = OzonAnalyticsClient("fake-client", "fake-key", transport=lambda endpoint, body:
                                    calls.append((endpoint, body)) or {"result": {"data": []}})
        client.get_data("2026-10-01", "2026-10-02", ["revenue", "ordered_units"], offset=1000)
        self.assertEqual("/v1/analytics/data", calls[0][0])
        self.assertEqual(["sku", "day"], calls[0][1]["dimension"])
        self.assertEqual(1000, calls[0][1]["offset"])
        for args in [("bad", "2026-10-02", ["revenue"]),
                     ("2026-10-02", "2026-10-01", ["revenue"]),
                     ("2026-10-01", "2026-10-02", ["invented_profit"])]:
            with self.assertRaises(ValueError):
                client.get_data(*args)
        self.assertEqual(1, len(calls))

    def test_snapshot_is_scoped_persisted_and_decimal_exact(self):
        client = OzonAnalyticsClient("fake-client", "fake-key", transport=lambda *_: {"result": {"data": [
            {"dimensions": [{"id": "1", "name": "Cup"}, {"id": "2026-10-01"}], "metrics": [0.1, 1]},
            {"dimensions": [{"id": "1", "name": "Cup"}, {"id": "2026-10-02"}], "metrics": [0.2, 2]},
        ]}})
        service = AnalyticsService(self.repo, client)
        service.refresh("a", "2026-10-01", "2026-10-02")
        data = AnalyticsService(AnalyticsRepository(self.root), None).read("a", "2026-10-01", "2026-10-02")
        self.assertEqual("0.3", data["totals"]["revenue"])
        self.assertEqual("3", data["totals"]["ordered_units"])
        self.assertIsNone(data["totals"]["hits_view"])
        self.assertEqual("RUB", data["currency"])
        self.assertEqual("NOT_SYNCED", service.read("b", "2026-10-01", "2026-10-02")["availability"])
        self.assertEqual("0.1", service.read("a", "2026-10-01", "2026-10-01")["totals"]["revenue"])

    def test_failed_refresh_is_visible_after_reopen_and_date_scoped(self):
        client = OzonAnalyticsClient("fake-client", "fake-key", transport=lambda *_: {"result": {"data": []}})
        AnalyticsService(self.repo, client).refresh("a", "2026-10-01", "2026-10-02")
        job, _ = self.erp.create_or_get_active_sync_job("OZON_ANALYTICS_SYNC", "a", entity_id="2026-10-01:2026-10-02:1")
        self.erp.update_sync_job(job, status="FAILED", message="读取失败；保留已有数据")
        reader = AnalyticsService(AnalyticsRepository(self.root), None)
        self.assertEqual("FAILED", reader.read("a", "2026-10-01", "2026-10-02")["last_refresh"]["status"])
        self.assertIsNone(reader.read("b", "2026-10-01", "2026-10-02")["last_refresh"])
        self.assertIsNone(reader.read("a", "2026-10-01", "2026-10-01")["last_refresh"])

    def test_failure_retains_snapshot(self):
        client = OzonAnalyticsClient("fake-client", "fake-key", transport=lambda *_: {"result": {"data": []}})
        service = AnalyticsService(self.repo, client)
        service.refresh("a", "2026-10-01", "2026-10-02")
        stamp = service.read("a", "2026-10-01", "2026-10-02")["fetched_at"]
        client.get_data = lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("fake-key"))
        with self.assertRaises(Exception):
            service.refresh("a", "2026-10-01", "2026-10-02")
        self.assertEqual(stamp, service.read("a", "2026-10-01", "2026-10-02")["fetched_at"])

    def test_optional_permission_failure_does_not_erase_core(self):
        from backend.integrations.ozon.base_client import OzonApiError
        def transport(endpoint, body):
            if "hits_view" in body["metrics"]:
                raise OzonApiError(endpoint, "No premium", 403)
            return {"result": {"data": []}}
        service = AnalyticsService(self.repo, OzonAnalyticsClient("fake-client", "fake-key", transport=transport))
        service.refresh("a", "2026-10-01", "2026-10-02", extra_metrics=["hits_view"])
        result = service.read("a", "2026-10-01", "2026-10-02")
        self.assertEqual("0", result["totals"]["revenue"])
        self.assertIsNone(result["totals"]["hits_view"])
        self.assertEqual("NO_PERMISSION", result["metrics_status"]["hits_view"])

    def test_incomplete_or_invalid_rows_do_not_publish_success(self):
        client = OzonAnalyticsClient("fake-client", "fake-key", transport=lambda *_:
                                    {"result": {"data": [{"dimensions": [], "metrics": [1]}]}})
        with self.assertRaises(ValueError):
            AnalyticsService(self.repo, client).refresh("a", "2026-10-01", "2026-10-02")
        self.assertEqual("NOT_SYNCED", AnalyticsService(self.repo, None).read("a", "2026-10-01", "2026-10-02")["availability"])

    def test_abc_and_funnel_do_not_invent_unknown_data(self):
        rows = [{"sku": "a", "revenue": "80"}, {"sku": "b", "revenue": "15"}, {"sku": "c", "revenue": "5"}]
        self.assertEqual(["A", "B", "C"], [row["class"] for row in abc_analysis(rows, "revenue")])
        self.assertEqual([], abc_analysis(rows, "revenue", complete=False))
        self.assertEqual([], abc_analysis([{"sku": "a", "revenue": "0"}], "revenue"))
        self.assertEqual([], abc_analysis([{"sku": "a", "revenue": None}], "revenue"))
        self.assertIsNone(funnel({"hits_view": "0", "hits_tocart": "0", "ordered_units": "0"})["view_to_cart"])
        self.assertIsNone(funnel({"hits_view": None, "hits_tocart": "1", "ordered_units": "1"})["view_to_cart"])
        self.assertEqual(Decimal("25"), Decimal(funnel({"hits_view": "100", "hits_tocart": "25", "ordered_units": "5"})["view_to_cart"]))

    def test_different_date_ranges_are_not_deduplicated_into_one_job(self):
        first, created = self.erp.create_or_get_active_sync_job("OZON_ANALYTICS_SYNC", "a", entity_id="2026-10-01:2026-10-02")
        same, duplicate = self.erp.create_or_get_active_sync_job("OZON_ANALYTICS_SYNC", "a", entity_id="2026-10-01:2026-10-02")
        other, other_created = self.erp.create_or_get_active_sync_job("OZON_ANALYTICS_SYNC", "a", entity_id="2026-09-01:2026-09-02")
        self.assertTrue(created)
        self.assertFalse(duplicate)
        self.assertEqual(first, same)
        self.assertTrue(other_created)
        self.assertNotEqual(first, other)

    def test_cancel_does_not_publish_an_incomplete_snapshot(self):
        job = self.erp.create_sync_job("OZON_ANALYTICS_SYNC", "a")
        self.erp.request_sync_job_cancel(job)
        client = OzonAnalyticsClient("fake-client", "fake-key", transport=lambda *_: self.fail("cancelled job made request"))
        with self.assertRaises(InterruptedError):
            AnalyticsService(self.repo, client).refresh("a", "2026-10-01", "2026-10-02", job_id=job)
        self.assertIsNone(self.repo.latest("a", "2026-10-01", "2026-10-02"))


if __name__ == "__main__":
    unittest.main()
