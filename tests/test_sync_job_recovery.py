from __future__ import annotations

import unittest

from tests.ozon_sync_support import SyncCase


class SyncJobRecoveryTest(SyncCase, unittest.TestCase):
    def test_startup_interrupts_only_unfinished_jobs(self):
        ids = {}
        for status in ("QUEUED", "RUNNING", "RETRYING", "SUCCEEDED", "PARTIAL", "FAILED"):
            job_id = self.create_job()
            self.repo.update_sync_job(job_id, status=status)
            ids[status] = job_id

        self.assertEqual(3, self.repo.interrupt_stale_sync_jobs())

        for status, job_id in ids.items():
            job = self.repo.get_sync_job(job_id)
            if status in {"QUEUED", "RUNNING", "RETRYING"}:
                self.assertEqual("INTERRUPTED", job["status"])
                self.assertEqual("INTERRUPTED", job["stage"])
                self.assertIsNotNone(job["finished_at"])
                self.assertIn("restart", job["message"].lower())
            else:
                self.assertEqual(status, job["status"])


if __name__ == "__main__":
    unittest.main()
