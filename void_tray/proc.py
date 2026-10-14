"""Process helpers."""

import os
import signal
import subprocess


def _kill_process_group(proc, grace=5):
    """SIGTERM the whole process group (sudo relays it to the root-owned
    child), then SIGKILL after a grace period. The child may be root-owned,
    so EPERM from killpg is expected and ignored."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            continue
        except Exception:
            return
