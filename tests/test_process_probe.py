import os
import tempfile
import unittest
from unittest.mock import patch
from unittest.mock import MagicMock


class ProcessProbeTest(unittest.TestCase):
    def test_windows_probe_never_signals_or_terminates_process(self):
        from scripts.process_probe import process_exists
        with patch('scripts.process_probe.os.name','nt'), patch('scripts.process_probe._windows_exists',return_value=True), patch('scripts.process_probe.os.kill') as kill:
            self.assertTrue(process_exists(123))
            kill.assert_not_called()

    def test_own_process_exists_and_invalid_ids_refused(self):
        from scripts.process_probe import process_exists
        self.assertTrue(process_exists(os.getpid()))
        for value in (0,-1,None,'not-a-pid'):
            self.assertFalse(process_exists(value))

    def test_registered_identity_rejects_reused_pid_and_wrong_project(self):
        from scripts.process_probe import process_identity,registered_process_matches
        from pathlib import Path
        root=Path(tempfile.gettempdir())/'erp-probe'
        with patch('scripts.process_probe.creation_token',return_value='first'):
            record=process_identity(123,root,'scripts/run_batch.py')
            self.assertTrue(registered_process_matches(123,record,root,'scripts/run_batch.py'))
            self.assertFalse(registered_process_matches(123,record,root/'other','scripts/run_batch.py'))
        with patch('scripts.process_probe.creation_token',return_value='reused'):
            self.assertFalse(registered_process_matches(123,record,root,'scripts/run_batch.py'))
        self.assertFalse(registered_process_matches(123,{},root,'scripts/run_batch.py'))

    def test_dead_windows_pid_and_access_denied_are_conservative(self):
        from scripts.process_probe import _windows_exists
        kernel=MagicMock();kernel.OpenProcess.return_value=None
        with patch('ctypes.WinDLL',return_value=kernel),patch('ctypes.get_last_error',return_value=87):
            self.assertFalse(_windows_exists(123))
        with patch('ctypes.WinDLL',return_value=kernel),patch('ctypes.get_last_error',return_value=5):
            self.assertTrue(_windows_exists(123))
        kernel.CloseHandle.assert_not_called()

    def test_exited_or_reused_pid_does_not_imply_service_identity(self):
        from scripts.process_probe import _windows_exists
        from scripts.candidate_server import matching_health
        kernel=MagicMock();kernel.OpenProcess.return_value=100
        kernel.GetExitCodeProcess.side_effect=lambda _handle,code:(setattr(code._obj,'value',0) or 1)
        with patch('ctypes.WinDLL',return_value=kernel):self.assertFalse(_windows_exists(123))
        kernel.CloseHandle.assert_called_once_with(100)
        # A live PID alone must never authorize accepting or stopping a listener.
        from pathlib import Path
        self.assertFalse(matching_health({'status':'ok','root':'other','service':'collector-local-ingest'},Path(tempfile.gettempdir()),'READ_ONLY','current'))


if __name__=='__main__':unittest.main()
