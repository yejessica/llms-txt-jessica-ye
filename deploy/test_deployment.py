"""Offline checks for the deployment-specific safeguards."""
import asyncio
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3
import sys

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.database import Database
from deploy.api import SubmissionLimit
from deploy.backup import backup, verify


def test_submission_limit_leaves_polling_unrestricted(monkeypatch):
    monkeypatch.setenv("LLMS_SUBMISSIONS_PER_MINUTE", "6")
    monkeypatch.setenv("LLMS_SUBMISSION_BURST", "2")

    async def upstream(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    limited = SubmissionLimit(upstream)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=limited), base_url="http://test") as client:
            assert (await client.post("/api/generate")).status_code == 200
            assert (await client.post("/api/websites/site/refresh")).status_code == 200
            rejected = await client.post("/api/generate")
            assert rejected.status_code == 429
            assert 1 <= int(rejected.headers["retry-after"]) <= 10
            for _ in range(20):
                assert (await client.get("/api/jobs/job")).status_code == 200
            assert (await client.get("/api/health")).status_code == 200
            # A ten-second refill grants a token without sleeping in the test.
            limited.updated -= 10
            assert (await client.post("/api/generate")).status_code == 200

    asyncio.run(exercise())


def test_live_wal_backup_and_isolated_recovery(tmp_path):
    source = tmp_path / "live.sqlite3"
    db = Database(source)
    db.initialize()
    markdown = "# A stored website\n"
    content_hash = hashlib.sha256(markdown.encode()).hexdigest()
    # Keep an open connection so committed rows can remain in the source WAL.
    with closing(sqlite3.connect(source)) as live:
        live.execute("INSERT INTO websites(website_id,original_url,normalized_url,created_at,status) VALUES ('site','https://example.com','https://example.com/',1,'completed')")
        live.execute("INSERT INTO generations VALUES ('generation','site',?,?,1,1)", (markdown, content_hash))
        live.commit()
        assert source.with_name(source.name + "-wal").exists()
        snapshot = backup(source)
        assert verify(snapshot)["generations"] == 1
        assert live.execute("PRAGMA journal_mode").fetchone() == ("wal",)
        assert not live.execute("SELECT name FROM sqlite_master WHERE name='recovery_probe'").fetchall()
        with closing(sqlite3.connect(snapshot)) as recovered:
            assert recovered.execute("PRAGMA journal_mode").fetchone() == ("delete",)
            recovered.execute("UPDATE generations SET markdown='corrupt output'")
            recovered.commit()
        with pytest.raises(RuntimeError, match="hash mismatch"):
            verify(snapshot)
