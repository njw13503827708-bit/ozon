from backend.db.erp import initialize as initialize_erp_test_database
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from fastapi import FastAPI

from backend.repositories.erp import ErpRepository


class ReviewApiTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        initialize_erp_test_database(self.root)
        erp = ErpRepository(self.root)
        erp.upsert_store({"id": "ozon", "platform": "OZON", "credential_ref": "api-secret"})
        erp.upsert_store({"id": "kaspi", "platform": "KASPI"})
        erp.upsert_product({"id": "mug", "title_cn": "Mug", "source_url": "C:\\secret"})
        from backend.repositories.reviews import ReviewRepository
        self.repo = ReviewRepository(self.root)
        self.first = self.repo.upsert_review({
            "store_id": "ozon", "external_id": "ozon-1", "text": "Great", "rating": 5,
            "product_id": "mug", "reply_status": "UNREPLIED", "published_at": "2026-09-25T00:00:00+00:00",
            "media": [{"media_type": "IMAGE", "source_url": "https://cdn.example.test/one.jpg"}],
        })
        self.repo.upsert_review({
            "store_id": "ozon", "external_id": "ozon-2", "text": "Average", "rating": 3,
            "reply_status": "REPLIED", "published_at": "2026-09-26T00:00:00+00:00",
        })
        self.repo.upsert_review({
            "store_id": "kaspi", "external_id": "kaspi-1", "text": "Bad", "rating": 1,
            "reply_status": "UNKNOWN", "published_at": "2026-09-27T00:00:00+00:00",
        })

    def request(self, path, query=None):
        from backend.api import reviews
        app = FastAPI()
        app.include_router(reviews.router)

        async def run():
            sent = []
            received = False

            async def receive():
                nonlocal received
                if not received:
                    received = True
                    return {"type": "http.request", "body": b"", "more_body": False}
                return {"type": "http.disconnect"}

            async def send(message):
                sent.append(message)

            with patch.object(reviews, "ROOT", self.root):
                await app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                           "method": "GET", "scheme": "http", "path": path, "raw_path": path.encode(),
                           "query_string": urlencode(query or {}).encode(), "headers": [],
                           "client": ("127.0.0.1", 1), "server": ("test", 80), "root_path": ""}, receive, send)
            status = next(message["status"] for message in sent if message["type"] == "http.response.start")
            body = b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body").decode()
            for secret in ("api-secret", "credential_ref", "C:\\secret"):
                self.assertNotIn(secret, body)
            return status, json.loads(body)

        return asyncio.run(run())

    def test_list_filters_paginate_and_include_time_bounds(self):
        status, body = self.request("/api/erp/reviews", {
            "platform": "OZON", "store_id": "ozon", "product_id": "mug", "rating": 5,
            "reply_status": "UNREPLIED", "published_from": "2026-09-25T00:00:00+00:00",
            "published_to": "2026-09-25T00:00:00+00:00", "page": 1, "page_size": 1,
        })
        self.assertEqual(200, status)
        self.assertEqual(1, body["total"])
        self.assertEqual([self.first], [row["id"] for row in body["items"]])
        next_page = self.request("/api/erp/reviews", {"page": 2, "page_size": 2})[1]
        self.assertEqual((3, 2, 2, 1), (next_page["total"], next_page["page"], next_page["page_size"], len(next_page["items"])))

    def test_detail_returns_mapping_and_media_and_missing_is_404(self):
        status, detail = self.request(f"/api/erp/reviews/{self.first}")
        self.assertEqual(200, status)
        self.assertEqual(("mug", "MAPPED", 5), (detail["product_id"], detail["mapping_status"], detail["rating"]))
        self.assertEqual("https://cdn.example.test/one.jpg", detail["media"][0]["source_url"])
        self.assertEqual(404, self.request("/api/erp/reviews/missing")[0])

    def test_each_filter_selects_only_matching_reviews(self):
        cases = (
            ({"platform": "OZON"}, {"ozon-1", "ozon-2"}),
            ({"store_id": "kaspi"}, {"kaspi-1"}),
            ({"product_id": "mug"}, {"ozon-1"}),
            ({"rating": 3}, {"ozon-2"}),
            ({"reply_status": "REPLIED"}, {"ozon-2"}),
            ({"published_from": "2026-09-26T00:00:00+00:00",
              "published_to": "2026-09-26T00:00:00+00:00"}, {"ozon-2"}),
        )
        for query, expected in cases:
            with self.subTest(query=query):
                status, body = self.request("/api/erp/reviews", query)
                self.assertEqual(200, status)
                self.assertEqual(expected, {row["external_id"] for row in body["items"]})

    def test_invalid_filters_fail_closed(self):
        for query in ({"rating": 6}, {"page": 0}, {"platform": "UNKNOWN"},
                      {"reply_status": "SENT"}, {"published_from": "not-a-date"},
                      {"published_from": "2026-09-27T00:00:00Z", "published_to": "2026-09-25T00:00:00Z"}):
            with self.subTest(query=query):
                self.assertEqual(422, self.request("/api/erp/reviews", query)[0])


if __name__ == "__main__":
    unittest.main()
