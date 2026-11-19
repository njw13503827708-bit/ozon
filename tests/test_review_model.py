from backend.db.erp import initialize as initialize_erp_test_database
import tempfile
import unittest
from pathlib import Path

from backend.db.erp import database, initialize
from backend.repositories.erp import ErpRepository


class ReviewModelTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        initialize_erp_test_database(self.root)
        self.erp = ErpRepository(self.root)
        self.erp.upsert_store({"id": "ozon", "platform": "OZON"})
        self.erp.upsert_store({"id": "kaspi", "platform": "KASPI"})
        self.erp.upsert_product({"id": "product", "title_cn": "Mug"})
        self.erp.upsert_listing({"store_id": "ozon", "product_id": "product", "offer_id": "offer"})

    def repo(self):
        from backend.repositories.reviews import ReviewRepository
        return ReviewRepository(self.root)

    def test_review_media_and_unknown_mapping_are_persisted_without_fabricated_ids(self):
        repo = self.repo()
        review_id = repo.upsert_review({
            "store_id": "ozon", "platform": "OZON", "external_id": "remote-1",
            "text": "Good mug", "rating": 5, "reply_status": "UNREPLIED",
            "published_at": "2026-09-25T09:00:00+00:00",
            "media": [{"media_type": "IMAGE", "source_url": "https://cdn.example.test/a.jpg", "external_id": "media-1"}],
        })
        row = repo.get_review(review_id)
        self.assertEqual(("Good mug", 5, "OZON", "ozon", "remote-1", "UNREPLIED"),
                         tuple(row[k] for k in ("text", "rating", "platform", "store_id", "external_id", "reply_status")))
        self.assertEqual((None, None, "UNMAPPED"), tuple(row[k] for k in ("product_id", "listing_id", "mapping_status")))
        self.assertEqual("2026-09-25T09:00:00+00:00", row["published_at"])
        self.assertEqual([{"media_type": "IMAGE", "source_url": "https://cdn.example.test/a.jpg", "external_id": "media-1"}],
                         [{k: media[k] for k in ("media_type", "source_url", "external_id")} for media in row["media"]])
        self.assertEqual(review_id, repo.upsert_review({"store_id": "ozon", "platform": "OZON", "external_id": "remote-1", "text": "Updated", "rating": 4}))
        self.assertEqual(1, repo.list_reviews()["total"])
        self.assertEqual("Updated", repo.get_review(review_id)["text"])
        self.assertNotEqual(review_id, repo.upsert_review({"store_id": "kaspi", "platform": "KASPI", "external_id": "remote-1", "text": "Other", "rating": 4}))

    def test_rating_scope_and_product_listing_mapping_reject_invalid_data(self):
        repo = self.repo()
        for rating in (0, 6):
            with self.subTest(rating=rating), self.assertRaises(ValueError):
                repo.upsert_review({"store_id": "ozon", "external_id": f"r{rating}", "text": "bad", "rating": rating})
        with self.assertRaises(ValueError):
            repo.upsert_review({"store_id": "ozon", "platform": "KASPI", "external_id": "wrong", "text": "bad", "rating": 3})
        with self.assertRaises(ValueError):
            repo.upsert_review({"store_id": "kaspi", "external_id": "wrong-listing", "text": "bad", "rating": 3, "listing_id": 1})
        mapped = repo.upsert_review({"store_id": "ozon", "external_id": "mapped", "text": "ok", "rating": 3, "product_id": "product", "listing_id": 1})
        self.assertEqual(("product", 1, "MAPPED"), tuple(repo.get_review(mapped)[k] for k in ("product_id", "listing_id", "mapping_status")))
        with database(self.root) as db:
            db.execute("INSERT INTO organizations(id,name,created_at) VALUES('other','Other','now')")
            db.execute("INSERT INTO products(id,source_platform,status,created_at,updated_at,organization_id) VALUES('foreign','x','DRAFT','now','now','other')")
            db.execute("""INSERT INTO stores(id,platform,display_name,currency,connection_status,credential_ref,
                       created_at,updated_at,organization_id) VALUES('foreign-store','OZON','Foreign','RUB',
                       'UNKNOWN','','now','now','other')""")
        with self.assertRaises(ValueError):
            repo.upsert_review({"store_id": "ozon", "external_id": "cross-org", "text": "bad", "rating": 3, "product_id": "foreign"})
        with self.assertRaises(ValueError):
            repo.upsert_review({"store_id": "foreign-store", "organization_id": "default",
                                "external_id": "cross-org-store", "text": "bad", "rating": 3})
        repo.upsert_review({"store_id": "foreign-store", "organization_id": "other",
                            "external_id": "mapped", "text": "valid other org", "rating": 3})
        with database(self.root) as db:
            self.assertEqual(2, db.execute("SELECT COUNT(*) FROM reviews WHERE external_id='mapped'").fetchone()[0])

    def test_repeat_sync_keeps_verified_mapping_and_reply_state_when_omitted(self):
        repo = self.repo()
        review_id = repo.upsert_review({"store_id": "ozon", "external_id": "remote-repeat", "text": "Old",
                                        "rating": 4, "product_id": "product", "listing_id": 1,
                                        "reply_status": "REPLIED", "published_at": "2026-09-25T09:00:00+00:00"})
        repo.upsert_review({"store_id": "ozon", "external_id": "remote-repeat", "text": "New", "rating": 5})
        row = repo.get_review(review_id)
        self.assertEqual(("product", 1, "MAPPED", "REPLIED"),
                         tuple(row[key] for key in ("product_id", "listing_id", "mapping_status", "reply_status")))
        self.assertEqual("2026-09-25T09:00:00+00:00", row["published_at"])

    def test_listing_retarget_clears_review_mapping_without_blocking_existing_upsert(self):
        repo = self.repo()
        review_id = repo.upsert_review({"store_id": "ozon", "external_id": "retarget", "text": "Old",
                                        "rating": 4, "product_id": "product", "listing_id": 1})
        self.erp.upsert_product({"id": "replacement", "title_cn": "Other"})
        self.erp.upsert_listing({"store_id": "ozon", "product_id": "replacement", "offer_id": "offer"})
        row = repo.get_review(review_id)
        self.assertEqual((None, None, "UNMAPPED"),
                         tuple(row[key] for key in ("product_id", "listing_id", "mapping_status")))
        self.assertEqual("replacement", self.erp.get_listing("ozon", "offer")["product_id"])

    def test_malformed_timestamp_or_media_is_value_error_without_partial_write(self):
        repo = self.repo()
        review_id = repo.upsert_review({"store_id": "ozon", "external_id": "safe", "text": "Saved", "rating": 4})
        bad_values = ({"published_at": 42}, {"media": None}, {"media": [{"media_type": "IMAGE"}]})
        for bad in bad_values:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                repo.upsert_review({"store_id": "ozon", "external_id": "safe", "text": "Corrupted",
                                    "rating": 5, **bad})
            self.assertEqual("Saved", repo.get_review(review_id)["text"])
            self.assertEqual(1, repo.list_reviews()["total"])

    def test_010_upgrade_preserves_existing_rows_and_is_repeatable(self):
        with database(self.root) as db:
            before = {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                      for table in ("stores", "products", "marketplace_listings")}
            # Reconstruct the exact 009 state by removing only 010-owned objects.
            db.execute("DROP TRIGGER listing_retarget_unmaps_reviews")
            db.execute("DROP TRIGGER reviews_scope_update")
            db.execute("DROP TRIGGER reviews_scope_insert")
            db.execute("DROP TABLE review_media")
            db.execute("DROP TABLE reviews")
            db.execute("DELETE FROM schema_migrations WHERE version='010'")
        initialize(self.root)
        initialize(self.root)
        with database(self.root) as db:
            self.assertEqual(1, db.execute("SELECT COUNT(*) FROM schema_migrations WHERE version='010'").fetchone()[0])
            self.assertEqual(before, {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in before})
            self.assertEqual([], db.execute("PRAGMA foreign_key_check").fetchall())


if __name__ == "__main__":
    unittest.main()
