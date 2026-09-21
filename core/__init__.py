"""Shared helpers for SKY's core package."""
import ctypes
import sys

_kernel32 = None


def _k32():
    global _kernel32
    if _kernel32 is None:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateMutexW.restype = ctypes.c_void_p
        k.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        _kernel32 = k
    return _kernel32


def single_instance(mutex_name: str) -> bool:
    """True if this is the only instance (Win32 named mutex held for process
    lifetime). False = another copy already owns it — exit quietly.

    Watchdog's WMI detection can return partial results under load and spawn
    a second copy; this makes duplicates structurally impossible (same
    pattern as the iron HUD's SKY_IRON_SINGLETON).
    """
    if not sys.platform.startswith("win"):
        return True
    try:
        k32 = _k32()
        handle = k32.CreateMutexW(None, False, mutex_name)
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            if handle:
                k32.CloseHandle(handle)  # leaked handle keeps the name alive
            return False
        return bool(handle)  # fresh owner: hold handle for process lifetime
    except Exception:
        return True  # never block startup on a mutex hiccup
