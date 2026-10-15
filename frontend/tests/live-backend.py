"""Opt-in browser-test fixture: real API/worker, isolated temporary SQLite DB.

No production data or crawler changes. Public DNS/SSRF checks remain enabled.
"""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

BACKEND = Path(__file__).resolve().parents[2] / "backend"


def interrupted(*_):
    raise KeyboardInterrupt


signal.signal(signal.SIGTERM, interrupted)
with tempfile.TemporaryDirectory(prefix="llms-frontend-live-") as directory:
    env = {
        **os.environ,
        "LLMS_DB_PATH": str(Path(directory) / "live.sqlite3"),
        "LLMS_MAX_PAGES": "1",
        "LLMS_MAX_DEPTH": "0",
        "LLMS_CHECK_INTERVAL": "86400",
        "LLMS_POLL_INTERVAL": "0.1",
        "LLMS_JOB_TIMEOUT": "45",
    }
    children = []
    try:
        children.append(subprocess.Popen([
            sys.executable, "-m", "uvicorn", "app.api.main:app", "--host", "127.0.0.1", "--port", "8100"
        ], cwd=BACKEND, env=env, start_new_session=True))
        children.append(subprocess.Popen([sys.executable, "-m", "app.worker"], cwd=BACKEND, env=env, start_new_session=True))
        while all(process.poll() is None for process in children):
            time.sleep(0.2)
        raise RuntimeError("API or worker exited unexpectedly")
    except KeyboardInterrupt:
        pass
    finally:
        for process in children:
            if process.poll() is None:
                process.terminate()
        for process in children:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
