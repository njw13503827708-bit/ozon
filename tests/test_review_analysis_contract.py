from backend.db.erp import initialize as initialize_erp_test_database
import socket
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.db.erp import database, initialize
from backend.repositories.erp import ErpRepository
from backend.repositories.reviews import ReviewRepository
from backend.services.review_analysis import ReviewAnalysisResult, ReviewAnalyzer


class ReviewAnalysisContractTest(unittest.TestCase):
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
            "store_id": "ozon", "external_id": "r-1", "text": "Broken lid", "rating": 2,
            "reply_status": "UNREPLIED",
        })

    def test_typed_result_persists_in_review_scope_without_running_or_drafting(self):
        class StubAnalyzer:
            def analyze(self, review):
                return ReviewAnalysisResult(
                    sentiment="NEGATIVE", topic="lid", issue_type="DAMAGE",
                    ai_summary="Lid arrived broken", suggested_reply="Please contact support",
                    confidence=0.85, analysis_status="COMPLETED",
                )

        analyzer: ReviewAnalyzer = StubAnalyzer()
        with patch.object(socket, "create_connection", side_effect=AssertionError("network call")):
            result = analyzer.analyze(self.repo.get_review(self.review_id))
            saved = self.repo.save_analysis(self.review_id, "ozon", result)
            listed = self.repo.list_analyses(self.review_id, "ozon")
        self.assertEqual([saved], listed["items"])
        self.assertEqual((self.review_id, "default", "OZON", "ozon"),
                         tuple(saved[key] for key in ("review_id", "organization_id", "platform", "store_id")))
        self.assertEqual(("NEGATIVE", "lid", "DAMAGE", "Lid arrived broken", "Please contact support", 0.85, "COMPLETED"),
                         tuple(saved[key] for key in ("sentiment", "topic", "issue_type", "ai_summary",
                                                       "suggested_reply", "confidence", "analysis_status")))
        with database(self.root) as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM review_reply_drafts").fetchone()[0])
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM sync_jobs").fetchone()[0])
        self.assertEqual("UNREPLIED", self.repo.get_review(self.review_id)["reply_status"])

    def test_invalid_confidence_or_status_is_rejected_before_persistence(self):
        for confidence in (-0.01, 1.01, float("nan"), True):
            with self.subTest(confidence=confidence), self.assertRaises(ValueError):
                ReviewAnalysisResult(confidence=confidence, analysis_status="COMPLETED")
        with self.assertRaises(ValueError):
            ReviewAnalysisResult(confidence=0.5, analysis_status="SENT")
        with database(self.root) as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM review_analyses").fetchone()[0])

    def test_repository_and_database_reject_cross_store_or_retargeted_analysis(self):
        result = ReviewAnalysisResult(analysis_status="COMPLETED")
        self.assertIsNone(self.repo.save_analysis(self.review_id, "kaspi", result))
        self.assertIsNone(self.repo.list_analyses(self.review_id, "kaspi"))
        saved = self.repo.save_analysis(self.review_id, "ozon", result)
        with self.assertRaises(sqlite3.IntegrityError), database(self.root) as db:
            db.execute("UPDATE review_analyses SET store_id='kaspi', platform='KASPI' WHERE id=?", (saved["id"],))
        with self.assertRaises(sqlite3.IntegrityError), database(self.root) as db:
            db.execute("""INSERT INTO review_analyses(id,review_id,organization_id,platform,store_id,
                       analysis_status,created_at) VALUES('wrong',?,'default','KASPI','kaspi','COMPLETED','now')""",
                       (self.review_id,))
        with database(self.root) as db:
            db.execute("INSERT INTO organizations(id,name,created_at) VALUES('other','Other','now')")
            db.execute("""INSERT INTO stores(id,platform,display_name,currency,connection_status,credential_ref,
                       created_at,updated_at,organization_id) VALUES('foreign','OZON','Foreign','RUB',
                       'UNKNOWN','','now','now','other')""")
        foreign_id = self.repo.upsert_review({"store_id": "foreign", "organization_id": "other",
                                              "external_id": "private", "text": "Private", "rating": 1})
        self.assertIsNone(self.repo.save_analysis(foreign_id, "foreign", result))
        self.assertIsNone(self.repo.list_analyses(foreign_id, "foreign"))

    def test_012_upgrade_preserves_populated_011_and_repeats(self):
        draft = self.repo.create_draft(self.review_id, "ozon", "Local only", "MANUAL")
        with database(self.root) as db:
            db.execute("DROP TABLE review_analyses")
            db.execute("DELETE FROM schema_migrations WHERE version='012'")
        initialize(self.root)
        initialize(self.root)
        with database(self.root) as db:
            self.assertEqual(1, db.execute("SELECT COUNT(*) FROM schema_migrations WHERE version='012'").fetchone()[0])
            self.assertEqual(1, db.execute("SELECT COUNT(*) FROM reviews WHERE id=?", (self.review_id,)).fetchone()[0])
            self.assertEqual(1, db.execute("SELECT COUNT(*) FROM review_reply_drafts WHERE id=?", (draft["id"],)).fetchone()[0])
            self.assertEqual([], db.execute("PRAGMA foreign_key_check").fetchall())


if __name__ == "__main__":
    unittest.main()
