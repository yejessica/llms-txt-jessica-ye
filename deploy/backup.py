"""Consistent SQLite snapshot and isolated recovery rehearsal. No live restore.

Usage: python deploy/backup.py [--verify SNAPSHOT]
The verify command copies the snapshot into a temporary directory before
opening it, checks integrity/foreign keys, and verifies stored Markdown hashes.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile


def verify(snapshot):
    with tempfile.TemporaryDirectory(prefix="llms-restore-") as directory:
        restored = Path(directory) / "recovered.sqlite3"
        shutil.copyfile(snapshot, restored)
        with closing(sqlite3.connect(restored)) as connection:
            if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise RuntimeError("Backup integrity check failed")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError("Backup has broken foreign keys")
            rows = connection.execute("SELECT markdown,content_hash FROM generations").fetchall()
            for markdown, expected in rows:
                if hashlib.sha256(markdown.encode("utf-8")).hexdigest() != expected:
                    raise RuntimeError("Recovered Markdown hash mismatch")
            counts = {table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                      for table in ("websites", "jobs", "generations", "pages")}
            # Exercise WAL writes on the recovered copy without changing live data.
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE recovery_probe (value TEXT)")
            connection.execute("INSERT INTO recovery_probe VALUES ('restored')")
            connection.commit()
            assert connection.execute("SELECT value FROM recovery_probe").fetchone() == ("restored",)
        return counts


def backup(source):
    source = Path(source).resolve()
    directory = source.parent / "backups"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = directory / f"llms-{stamp}.sqlite3"
    temporary = destination.with_suffix(".partial")
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=10)) as live:
        with closing(sqlite3.connect(temporary)) as snapshot:
            live.backup(snapshot)
            snapshot.execute("PRAGMA journal_mode=DELETE")
    verify(temporary)
    temporary.replace(destination)
    # Retain the seven newest verified snapshots belonging to this script.
    for old in sorted(directory.glob("llms-*.sqlite3"))[:-7]:
        old.unlink()
    return destination


if __name__ == "__main__":
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.verify:
        print(verify(args.verify))
    else:
        path = os.environ["LLMS_DB_PATH"]
        print(backup(path), flush=True)
