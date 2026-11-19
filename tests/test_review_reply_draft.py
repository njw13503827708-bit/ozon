from backend.db.erp import initialize as initialize_erp_test_database
import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from fastapi import FastAPI

from backend.db.erp import database, initialize
from backend.repositories.erp import ErpRepository
from backend.repositories.reviews import ReviewRepository


class ReviewReplyDraftTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        initialize_erp_test_database(self.root)
        erp = ErpRepository(self.root)
        erp.upsert_store({"id": "ozon", "platform": "OZON"})
        erp.upsert_store({"id": "kaspi", "platform": "KASPI"})
        self.repo = ReviewRepository(self.root)
        self.review_id = self.repo.upsert_review({
            "store_id": "ozon", "external_id": "remote-1", "text": "Please help", "rating": 2,
            "reply_status": "UNREPLIED",
        })

    def request(self, method, path, body=None, query=None):
        from backend.api import reviews
        app = FastAPI()
        app.include_router(reviews.router)
        payload = json.dumps(body).encode() if body is not None else b""

        async def run():
            sent = []
            received = False

            async def receive():
                nonlocal received
                if not received:
                    received = True
                    return {"type": "http.request", "body": payload, "more_body": False}
                return {"type": "http.disconnect"}

            async def send(message):
                sent.append(message)

            with patch.object(reviews, "ROOT", self.root):
                await app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                           "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
                           "query_string": urlencode(query or {}).encode(),
                           "headers": [(b"content-type", b"application/json")],
                           "client": ("127.0.0.1", 1), "server": ("test", 80), "root_path": ""}, receive, send)
            status = next(message["status"] for message in sent if message["type"] == "http.response.start")
            content = b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")
            return status, json.loads(content)

        return asyncio.run(run())

    def test_create_list_update_are_local_and_preserve_platform_reply_status(self):
        path = f"/api/erp/reviews/{self.review_id}/drafts"
        status, created = self.request("POST", path, {"store_id": "ozon", "content": " We can help ", "source": "MANUAL"})
        self.assertEqual(201, status)
        self.assertEqual((self.review_id, "default", "ozon", "OZON", "We can help", "MANUAL", "DRAFT"),
                         tuple(created[key] for key in ("review_id", "organization_id", "store_id", "platform", "content", "source", "status")))
        self.assertTrue(created["created_at"])
        self.assertTrue(created["updated_at"])
        self.assertEqual([created], self.request("GET", path, query={"store_id": "ozon"})[1]["items"])
        status, updated = self.request("PATCH", f"{path}/{created['id']}",
                                       {"store_id": "ozon", "content": "Updated", "status": "DISCARDED"})
        self.assertEqual(200, status)
        self.assertEqual(("Updated", "DISCARDED"), (updated["content"], updated["status"]))
        self.assertEqual("UNREPLIED", self.repo.get_review(self.review_id)["reply_status"])
        with database(self.root) as db:
            self.assertEqual(1, db.execute("SELECT COUNT(*) FROM review_reply_drafts").fetchone()[0])

    def test_empty_content_cross_store_and_unknown_review_are_rejected(self):
        path = f"/api/erp/reviews/{self.review_id}/drafts"
        for body, expected in (({"store_id": "ozon", "content": "  ", "source": "MANUAL"}, 422),
                               ({"store_id": "kaspi", "content": "Reply", "source": "MANUAL"}, 404)):
            with self.subTest(body=body):
                self.assertEqual(expected, self.request("POST", path, body)[0])
        self.assertEqual(404, self.request("GET", path, query={"store_id": "kaspi"})[0])
        self.assertEqual(404, self.request("POST", "/api/erp/reviews/missing/drafts",
                                           {"store_id": "ozon", "content": "Reply", "source": "MANUAL"})[0])
        status, created = self.request("POST", path, {"store_id": "ozon", "content": "Valid", "source": "AI_SUGGESTION"})
        self.assertEqual(201, status)
        self.assertEqual("AI_SUGGESTION", created["source"])
        self.assertEqual(404, self.request("PATCH", f"{path}/{created['id']}",
                                           {"store_id": "kaspi", "content": "Cross-store"})[0])
        self.assertEqual("Valid", self.request("GET", path, query={"store_id": "ozon"})[1]["items"][0]["content"])

    def test_no_platform_reply_route_exists(self):
        for suffix in ("send", "publish", "reply-to-platform"):
            status, _ = self.request("POST", f"/api/erp/reviews/{self.review_id}/{suffix}", {})
            self.assertEqual(404, status)

    def test_database_rejects_cross_store_draft_retarget(self):
        draft = self.repo.create_draft(self.review_id, "ozon", "Local reply", "MANUAL")
        with self.assertRaises(sqlite3.IntegrityError), database(self.root) as db:
            db.execute("UPDATE review_reply_drafts SET store_id='kaspi', platform='KASPI' WHERE id=?", (draft["id"],))

    def test_default_organization_cannot_read_or_write_another_organizations_drafts(self):
        with database(self.root) as db:
            db.execute("INSERT INTO organizations(id,name,created_at) VALUES('other','Other','now')")
            db.execute("""INSERT INTO stores(id,platform,display_name,currency,connection_status,credential_ref,
                       created_at,updated_at,organization_id) VALUES('foreign-store','OZON','Foreign','RUB',
                       'UNKNOWN','','now','now','other')""")
        foreign_review = self.repo.upsert_review({"store_id": "foreign-store", "organization_id": "other",
                                                  "external_id": "foreign-review", "text": "Foreign", "rating": 1})
        with database(self.root) as db:
            db.execute("""INSERT INTO review_reply_drafts(id,review_id,organization_id,platform,store_id,
                       content,source,status,created_at,updated_at) VALUES('foreign-draft',?,'other','OZON',
                       'foreign-store','Private','MANUAL','DRAFT','now','now')""", (foreign_review,))
        path = f"/api/erp/reviews/{foreign_review}/drafts"
        self.assertEqual(404, self.request("GET", f"/api/erp/reviews/{foreign_review}")[0])
        self.assertEqual(404, self.request("GET", path, query={"store_id": "foreign-store"})[0])
        self.assertEqual(404, self.request("POST", path,
                                           {"store_id": "foreign-store", "content": "Leak", "source": "MANUAL"})[0])
        self.assertEqual(404, self.request("PATCH", f"{path}/foreign-draft",
                                           {"store_id": "foreign-store", "content": "Leak"})[0])
        with database(self.root) as db:
            self.assertEqual("Private", db.execute("SELECT content FROM review_reply_drafts WHERE id='foreign-draft'").fetchone()[0])

    def test_011_upgrade_preserves_010_reviews_and_is_repeatable(self):
        with database(self.root) as db:
            db.execute("DROP TABLE IF EXISTS review_reply_drafts")
            db.execute("DELETE FROM schema_migrations WHERE version='011'")
        initialize(self.root)
        initialize(self.root)
        with database(self.root) as db:
            self.assertEqual(1, db.execute("SELECT COUNT(*) FROM schema_migrations WHERE version='011'").fetchone()[0])
            self.assertEqual(1, db.execute("SELECT COUNT(*) FROM reviews WHERE id=?", (self.review_id,)).fetchone()[0])
            self.assertEqual([], db.execute("PRAGMA foreign_key_check").fetchall())


if __name__ == "__main__":
    unittest.main()
