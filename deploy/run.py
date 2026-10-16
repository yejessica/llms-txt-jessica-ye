"""Supervise the three existing processes in one Render service.

If any child exits, stop the others and fail so Render restarts the entire
service. Exactly one worker shares the API's local persistent SQLite file.
"""
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from deploy.backup import backup

ROOT = Path(__file__).resolve().parents[1]
log = logging.getLogger("deploy")


def main():
    os.umask(0o077)
    database = Path(os.environ["LLMS_DB_PATH"])
    if not database.is_absolute() or database.resolve().is_relative_to(ROOT):
        raise ValueError("LLMS_DB_PATH must be absolute and outside the repository")
    database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    port = int(os.environ.get("PORT", "10000"))
    if not 1 <= port <= 65535 or port == 8000:
        raise ValueError("PORT must be valid and different from the private API port")
    env = {**os.environ, "BACKEND_URL": "http://127.0.0.1:8000",
           "NODE_ENV": "production", "PYTHONUNBUFFERED": "1",
           "PYTHONPATH": os.pathsep.join([str(ROOT / "backend"), str(ROOT)])}
    commands = [
        ("api", [sys.executable, "-m", "uvicorn", "deploy.api:app", "--host", "127.0.0.1",
                 "--port", "8000", "--workers", "1"], ROOT),
        ("worker", [sys.executable, "-m", "app.worker"], ROOT / "backend"),
        ("frontend", ["node", "node_modules/next/dist/bin/next", "start", "--hostname",
                      "0.0.0.0", "--port", str(port)], ROOT / "frontend"),
    ]
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, stop)
    children = []
    failed = False
    try:
        for name, command, cwd in commands:
            if stopping:
                break
            process = subprocess.Popen(command, cwd=cwd, env=env, start_new_session=True)
            children.append((name, process))
            log.info("Started %s pid=%s", name, process.pid)
        next_backup = time.monotonic() + 30
        while not stopping:
            for name, process in children:
                if process.poll() is not None:
                    log.error("%s exited (%s); restarting the service", name, process.returncode)
                    failed = stopping = True
                    break
            if not stopping and time.monotonic() >= next_backup:
                try:
                    log.info("Verified backup: %s", backup(database))
                except Exception:
                    log.exception("Backup failed; inspect storage and retry")
                next_backup = time.monotonic() + 86400
            time.sleep(0.5)
    finally:
        for _, process in reversed(children):
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + 20
        for name, process in reversed(children):
            try:
                process.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                log.warning("Force stopping %s", name)
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
    return int(failed)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    raise SystemExit(main())
