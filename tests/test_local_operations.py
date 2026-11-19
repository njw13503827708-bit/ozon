import tempfile
import unittest
from pathlib import Path

from backend.repositories.local_operations import LocalOperationsRepository


class LocalOperationsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = LocalOperationsRepository(self.root)

    def test_reads_never_create_missing_database(self):
        with self.assertRaises(RuntimeError):
            self.repo.versions('a', 1)
        self.assertFalse(self.repo.path.exists())

    def test_initialize_is_explicit_repeatable_and_preserves_versions(self):
        self.repo.initialize()
        first = self.repo.save_version('a', 1, {'title_ru': 'Полка'}, 'revision', 0, 'manual')
        self.repo.initialize()
        self.assertEqual([first], self.repo.versions('a', 1))
        self.assertEqual([], self.repo.versions('b', 1))

    def test_concurrent_stale_expected_version_refused(self):
        self.repo.initialize()
        self.repo.save_version('a', 1, {'title_ru': 'Полка'}, 'revision', 0, 'manual')
        with self.assertRaises(RuntimeError):
            self.repo.save_version('a', 1, {'title_ru': 'Чужая правка'}, 'revision', 0, 'manual')
        self.assertEqual(1, len(self.repo.versions('a', 1)))

    def test_snapshots_are_shop_period_scoped_and_not_overwritten(self):
        self.repo.initialize()
        self.repo.save_snapshot('SEARCH', 'a', '2026-10-01', '2026-10-02', {'rows': [{'sku': '1'}], 'source': 'OFFLINE_TEST'})
        self.assertIsNone(self.repo.snapshot('SEARCH', 'b', '2026-10-01', '2026-10-02'))
        self.assertIsNone(self.repo.snapshot('SEARCH', 'a', '2026-10-03', '2026-10-04'))
        self.assertEqual('1', self.repo.snapshot('SEARCH', 'a', '2026-10-01', '2026-10-02')['data']['rows'][0]['sku'])

    def test_review_does_not_survive_new_edit_and_foreign_store_cannot_review(self):
        self.repo.initialize()
        self.repo.save_version('a', 1, {'title_ru': 'Полка'}, 'revision', 0, 'manual')
        reviewed = self.repo.review_version('a', 1, 1, 'revision', 'manual', {})
        self.assertEqual('REVIEWED', reviewed['status'])
        second = self.repo.save_version('a', 1, {'title_ru': 'Полка для ванной'}, 'revision', 1, 'manual')
        self.assertEqual('DRAFT', second['status'])
        with self.assertRaises(KeyError):
            self.repo.review_version('b', 1, 1, 'revision', 'manual', {})

    def test_startup_interrupt_does_not_change_foreign_organization_jobs(self):
        self.repo.initialize()
        local, _ = self.repo.start_job('ORDERS', 'a', 'period')
        with self.repo.connect(readonly=False) as db:
            db.execute("INSERT INTO jobs(id,organization_id,kind,store_id,scope,status,started_at) VALUES('foreign-job','foreign','ORDERS','a','period','RUNNING','now')")
        self.repo.interrupt_jobs()
        self.assertEqual('INTERRUPTED', self.repo.job(local['id'])['status'])
        with self.repo.connect() as db:
            self.assertEqual('RUNNING', db.execute("SELECT status FROM jobs WHERE id='foreign-job'").fetchone()[0])


if __name__ == '__main__':
    unittest.main()
