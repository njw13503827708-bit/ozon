import tempfile
import unittest
import importlib
import subprocess
import sys
from unittest.mock import patch
from types import SimpleNamespace
from pathlib import Path


class CandidateRuntimeTest(unittest.TestCase):
    def test_windows_launcher_detaches_candidate_without_changing_policy(self):
        from scripts.launch_candidate import spawn_windows_server
        root=Path(tempfile.gettempdir())/'ozon自动化 牛'/'候选'
        with patch('scripts.launch_candidate.subprocess.Popen',return_value=SimpleNamespace(pid=88)) as launch:
            self.assertEqual(88,spawn_windows_server(['python.exe','scripts/candidate_server.py','--port','8911'],root))
        args,kwargs=launch.call_args
        self.assertEqual(args[0],['python.exe','scripts/candidate_server.py','--port','8911'])
        self.assertEqual(kwargs['cwd'],root)
        self.assertTrue(kwargs['creationflags'] & subprocess.CREATE_BREAKAWAY_FROM_JOB)
        self.assertTrue(kwargs['creationflags'] & subprocess.DETACHED_PROCESS)
        self.assertTrue(kwargs['creationflags'] & subprocess.CREATE_NEW_PROCESS_GROUP)
        self.assertNotIn('ExecutionPolicy',str(kwargs))
    def test_port_collision_never_accepts_other_root_or_mode(self):
        from scripts.candidate_server import matching_health
        root=Path(tempfile.gettempdir())/'candidate'
        health={'status':'ok','service':'collector-local-ingest','root':str(root),'mode':'READ_ONLY','build':'candidate-a'}
        self.assertTrue(matching_health(health,root,'READ_ONLY','candidate-a'))
        for key,value in (('root',str(root/'other')),('mode','LIVE'),('build','old')):
            self.assertFalse(matching_health({**health,key:value},root,'READ_ONLY','candidate-a'))

    def test_read_only_allows_local_configuration_but_blocks_platform_execution(self):
        from scripts.candidate_server import blocked_action
        self.assertFalse(blocked_action('/api/workbench/shops','POST','READ_ONLY'))
        self.assertFalse(blocked_action('/api/erp/stores/optimization-publication/review','POST','READ_ONLY'))
        self.assertTrue(blocked_action('/api/erp/stores/optimization-publication/confirm','POST','READ_ONLY'))
        self.assertTrue(blocked_action('/api/workbench/batches/B1/run','POST','READ_ONLY'))
        self.assertTrue(blocked_action('/api/erp/stores/a/orders/sync','POST','LIVE'))
        self.assertTrue(blocked_action('/api/workbench/products/P000123','DELETE','LIVE'))
        self.assertTrue(blocked_action('/api/workbench/products/P000123/run','POST','DEMO'))
        self.assertTrue(blocked_action('/api/workbench/products/P000123/stores/a/retry','POST','READ_ONLY'))
        self.assertTrue(blocked_action('/api/workbench/batches/B1/confirm-upload','POST','READ_ONLY'))
        self.assertFalse(blocked_action('/api/workbench/batches/B1/confirm-upload','POST','LIVE'))
        for mode in ('READ_ONLY','LIVE'):
            self.assertTrue(blocked_action('/api/workbench/market-intelligence/search-visibility/apply-batch','POST',mode))

    def test_new_database_bootstrap_never_overwrites_existing_database(self):
        from scripts.candidate_server import initialize_candidate
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/'runtime').mkdir();db=root/'runtime/erp.sqlite3';db.write_bytes(b'keep')
            with self.assertRaises(RuntimeError):initialize_candidate(root)
            self.assertEqual(b'keep',db.read_bytes())

    def test_windows_venv_redirector_pid_is_not_the_service_pid(self):
        from scripts.launch_candidate import observed_service_pid
        self.assertEqual(12345,observed_service_pid({'pid':12345}))
        for value in (None,True,-1,'12345'):
            with self.assertRaises(RuntimeError):observed_service_pid({'pid':value})

    def test_legacy_and_package_imports_share_protected_edit_lock(self):
        from scripts.batch_upload_approval import batch_upload_lock
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
        legacy=importlib.import_module('batch_upload_approval')
        with tempfile.TemporaryDirectory() as folder:
            with batch_upload_lock(Path(folder)):
                with legacy.batch_upload_lock(Path(folder)):pass


if __name__=='__main__':unittest.main()
