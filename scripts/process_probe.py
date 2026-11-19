"""Read-only process checks; Windows os.kill(pid, 0) is not a safe probe."""
import os
import subprocess
from pathlib import Path


def _windows_exists(pid):
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000,False,pid)
    if not handle:
        return ctypes.get_last_error()==5
    try:
        code = wintypes.DWORD()
        return bool(kernel.GetExitCodeProcess(handle,ctypes.byref(code))) and code.value==259
    finally:
        kernel.CloseHandle(handle)


def process_exists(value):
    try:
        pid = int(value)
        if pid <= 0:return False
        if os.name=='nt':return _windows_exists(pid)
        os.kill(pid,0)
        return True
    except (OSError,TypeError,ValueError,OverflowError):
        return False


def probe_process(value):
    if not process_exists(value):
        raise ProcessLookupError('Process is not running')


def creation_token(pid):
    try:
        if os.name=='nt':
            import ctypes
            from ctypes import wintypes
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
            kernel.GetProcessTimes.argtypes=[wintypes.HANDLE,*([ctypes.POINTER(wintypes.FILETIME)]*4)]
            kernel.CloseHandle.argtypes=[wintypes.HANDLE]
            handle=kernel.OpenProcess(0x1000,False,int(pid))
            if not handle:return None
            try:
                times=[wintypes.FILETIME() for _ in range(4)]
                if not kernel.GetProcessTimes(handle,*[ctypes.byref(value) for value in times]):return None
                return str((times[0].dwHighDateTime<<32)|times[0].dwLowDateTime)
            finally:kernel.CloseHandle(handle)
        result=subprocess.run(['ps','-o','lstart=','-p',str(int(pid))],capture_output=True,check=True,timeout=2)
        return result.stdout.decode('utf-8').strip() or None
    except (OSError,ValueError,subprocess.SubprocessError):return None


def process_identity(pid,root,entry):
    return {'pid':int(pid),'creation_token':creation_token(pid),'root':str(Path(root).resolve()),'entry':entry}


def registered_process_matches(pid,record,root,entry):
    if not record or record.get('pid')!=pid or record.get('root')!=str(Path(root).resolve()) or record.get('entry')!=entry or not record.get('creation_token'):
        return False
    return record['creation_token']==creation_token(pid)
