from backend.db.erp import initialize as initialize_erp_test_database
import json
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from urllib.parse import urlsplit
from tests.test_product_center_api import AsgiResponse

from backend.api import analytics
from backend.repositories.erp import ErpRepository
from backend.integrations.ozon.base_client import OzonApiError


class AnalyticsApiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        initialize_erp_test_database(self.root)
        ErpRepository(self.root).upsert_store({"id": "a", "platform": "OZON"})
        app = FastAPI()
        app.include_router(analytics.router)
        self.app = app

    def request(self, url, method="GET", body=None):
        parsed = urlsplit(url)
        async def run():
            messages = []
            async def receive():
                return {"type": "http.request", "body": json.dumps(body or {}).encode(), "more_body": False}
            async def send(message):
                messages.append(message)
            await self.app({"type": "http", "method": method, "path": parsed.path,
                            "query_string": parsed.query.encode(), "headers": [(b"content-type", b"application/json")],
                            "state": {}, "scheme": "http", "server": ("test", 80),
                            "client": ("127.0.0.1", 1)}, receive, send)
            return AsgiResponse(messages)
        return asyncio.run(run())

    def test_reads_local_status_without_credentials_or_remote_access(self):
        with patch.object(analytics, "ROOT", self.root), patch.object(analytics, "_store_credentials") as credentials:
            response = self.request("/api/erp/stores/a/analytics?date_from=2026-10-01&date_to=2026-10-02")
            self.assertEqual(200, response.status_code)
            self.assertEqual("NOT_SYNCED", response.json()["availability"])
            credentials.assert_not_called()
            self.assertNotIn("credential_ref", response.text)

    def test_host_gate_precedes_credentials_and_invalid_dates_fail(self):
        with patch.object(analytics, "ROOT", self.root), patch.object(analytics, "_store_credentials") as credentials:
            self.assertEqual(403, self.request("/api/erp/stores/a/analytics/refresh", "POST", {"date_from": "2026-10-01", "date_to": "2026-10-02"}).status_code)
            self.assertEqual(422, self.request("/api/erp/stores/a/analytics?date_from=x&date_to=y").status_code)
            credentials.assert_not_called()

    def test_read_only_routes_have_no_write_proxy(self):
        paths = {(route.path, tuple(sorted(route.methods or []))) for route in self.app.routes}
        self.assertIn(("/api/erp/stores/{store_id}/analytics", ("GET",)), paths)
        self.assertFalse(any("url" in path or "publish" in path for path, _ in paths))

    def test_refresh_failure_records_safe_actionable_reason(self):
        failures = [
            (OzonApiError("/v1/analytics/data", "fake-secret", 429), "HTTP 429"),
            (OzonApiError("/v1/analytics/data", "fake-secret", 503), "HTTP 503"),
            (OzonApiError("/v1/analytics/data", "fake-secret"), "连接失败"),
            (ValueError("fake-secret"), "数据格式"),
        ]
        for error, expected in failures:
            with self.subTest(expected=expected), patch.object(analytics, "ROOT", self.root), patch.object(analytics, "_store_credentials", return_value=({}, {"client_id": "offline", "api_key": "fake-secret"})), patch.object(analytics, "OzonAnalyticsClient") as client, patch.object(analytics.threading, "Thread") as thread:
                client.return_value.get_data.side_effect = error
                result = analytics.refresh_analytics("a", analytics.RefreshRequest(date_from="2026-10-01", date_to="2026-10-02"), None)
                thread.call_args.kwargs["target"]()
                job = ErpRepository(self.root).get_sync_job(result["job_id"])
                self.assertEqual("FAILED", job["status"])
                self.assertIn(expected, job["error"])
                self.assertIn("保留", job["message"])
                self.assertNotIn("fake-secret", job["error"])


if __name__ == "__main__":
    unittest.main()
