import sys,tempfile,unittest,sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from backend.db.erp import initialize,database,database_path
from backend.repositories.erp import ErpRepository
from backend.repositories.reviews import ReviewRepository
from backend.repositories.analytics import AnalyticsRepository
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'market-intelligence'),str(ROOT/'finance-center')]
from task_database import product_snapshot
from market_intelligence import MarketStore
from finance_center import FinanceCenter

class ReadBoundaryTest(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
 def test_missing_erp_is_not_created_by_repository(self):
  with self.assertRaises(RuntimeError): ErpRepository(self.root).list_stores()
  self.assertFalse(database_path(self.root).exists())
 def test_stale_erp_is_not_migrated_by_read(self):
  initialize(self.root)
  with closing(sqlite3.connect(database_path(self.root))) as db, db: db.execute("DELETE FROM schema_migrations WHERE version='014'")
  with self.assertRaises(RuntimeError): ErpRepository(self.root).list_stores()
  with closing(sqlite3.connect(database_path(self.root))) as db, db: self.assertIsNone(db.execute("SELECT 1 FROM schema_migrations WHERE version='014'").fetchone())
 def test_read_paths_emit_no_schema_or_business_writes(self):
  initialize(self.root);ErpRepository(self.root).upsert_store({'id':'s','platform':'OZON'})
  statements=[];original=sqlite3.connect
  def traced(*args,**kwargs):
   db=original(*args,**kwargs);db.set_trace_callback(statements.append);return db
  with patch('sqlite3.connect',side_effect=traced):
   self.assertEqual(1,len(ErpRepository(self.root).list_stores()))
   ReviewRepository(self.root).list_reviews()
   self.assertIsNone(AnalyticsRepository(self.root).latest('s','2026-09-26','2026-10-02'))
  writes=[s for s in statements if s.lstrip().split()[0].upper() in {'CREATE','ALTER','INSERT','UPDATE','DELETE','REPLACE'}]
  self.assertEqual([],writes)
 def test_readonly_connection_rejects_business_writes(self):
  initialize(self.root)
  with database(self.root,readonly=True) as db:
   with self.assertRaises(sqlite3.OperationalError):db.execute("DELETE FROM stores")
 def test_explicit_upgrade_preserves_data_and_is_repeatable(self):
  initialize(self.root);ErpRepository(self.root).upsert_store({'id':'s','platform':'OZON'})
  with closing(sqlite3.connect(database_path(self.root))) as db, db:
   db.execute('DROP TABLE analytics_snapshots');db.execute("DELETE FROM schema_migrations WHERE version='014'")
  initialize(self.root);initialize(self.root)
  self.assertEqual('s',ErpRepository(self.root).list_stores()[0]['id'])
 def test_missing_market_read_does_not_create_database(self):
  path=self.root/'market.sqlite3'
  with self.assertRaises(RuntimeError): MarketStore(path).counts()
  self.assertFalse(path.exists())
 def test_missing_finance_read_does_not_create_database(self):
  svc=FinanceCenter(self.root)
  with self.assertRaises(RuntimeError): svc.overview()
  self.assertFalse(svc.db_path.exists())
 def test_missing_task_read_does_not_create_database(self):
  with self.assertRaises(RuntimeError): product_snapshot(self.root,'P990001')
  self.assertEqual([],list(self.root.rglob('*.sqlite3')))

if __name__=='__main__':unittest.main()
