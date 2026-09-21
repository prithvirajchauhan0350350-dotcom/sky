"""spy_bridge — run SKY's Windows-side skill modules (spy_data.py, spy_sec.py,
spy_ops.py, automation.py, spy_vitals.py, spy_avatar.py, spy_hologram.py).

SKY runs natively on Windows, so these are plain in-process imports — the
call() signature is kept (module, function, timeout, **kwargs) so callers
don't care. Results are dicts from the module; exceptions surface directly.
"""
import importlib
import os
import sys

WIN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WIN_DIR_POSIX = WIN_DIR  # kept for callers that reference it

if WIN_DIR not in sys.path:
    sys.path.insert(0, WIN_DIR)


def call(module: str, function: str, timeout: int = 60, **kwargs):
    """Run `module.function(**kwargs)` in-process. `timeout` is accepted for
    call-site compatibility (Windows modules manage their own timeouts)."""
    mod = importlib.import_module(module)
    fn = getattr(mod, function)
    return fn(**kwargs)


def available() -> bool:
    try:
        importlib.import_module("spy_data")
        return True
    except Exception:
        return False


if __name__ == "__main__":
    # self-test: python core/spy_bridge.py
    res = call("spy_data", "simulate", kind="projectile",
               v0=25, angle_deg=45, drag=0.0)
    print("simulate ->", str(res)[:200])
    res = call("spy_sec", "password_strength", pw="correct horse battery staple")
    print("password_strength ->", str(res)[:200])
