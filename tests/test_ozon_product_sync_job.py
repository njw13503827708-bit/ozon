import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from backend.api.ozon_products import queue_catalog_sync
from tests.ozon_sync_support import SyncCase


class OzonProductSyncJobTest(SyncCase, unittest.TestCase):
    def test_queue_returns_before_the_worker_finishes(self):
        release = threading.Event()

        def worker(job_id, store_id):
            release.wait(1)

        started = time.perf_counter()
        result = queue_catalog_sync(self.repo, "store-1", worker)
        elapsed = time.perf_counter() - started
        release.set()

        self.assertLess(elapsed, 0.2)
        self.assertEqual("QUEUED", result["status"])
        self.assertTrue(result["job_id"])

    def test_concurrent_queue_requests_reuse_one_active_job(self):
        started = []
        release = threading.Event()

        def worker(job_id, store_id):
            started.append((job_id, store_id))
            release.wait(1)

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(
                pool.map(
                    lambda _: queue_catalog_sync(self.repo, "store-1", worker),
                    range(8),
                )
            )
        release.set()

        self.assertEqual(1, len({item["job_id"] for item in results}))
        self.assertEqual(1, len(started))
        self.assertEqual(1, sum(not item["reused"] for item in results))

    def test_unhandled_worker_exception_marks_job_failed_without_secret(self):
        def worker(job_id, store_id):
            raise RuntimeError("Api-Key=api-secret")

        result = queue_catalog_sync(self.repo, "store-1", worker)
        for _ in range(100):
            job = self.repo.get_sync_job(result["job_id"])
            if job["status"] == "FAILED":
                break
            time.sleep(0.01)

        self.assertEqual("FAILED", job["status"])
        self.assertEqual("FAILED", job["stage"])
        self.assertIsNotNone(job["finished_at"])
        self.assertNotIn("api-secret", job["error"])


if __name__ == "__main__":
    unittest.main()
