"""Small SQLite repository. Every state transition is a short transaction."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4


SCHEMA = """
CREATE TABLE IF NOT EXISTS websites (
    website_id TEXT PRIMARY KEY,
    original_url TEXT NOT NULL,
    normalized_url TEXT NOT NULL UNIQUE,
    title TEXT,
    created_at REAL NOT NULL,
    last_crawl_at REAL,
    last_successful_generation_at REAL,
    next_check_at REAL,
    status TEXT NOT NULL CHECK(status IN ('pending','running','completed','failed'))
);
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    website_id TEXT NOT NULL REFERENCES websites(website_id),
    job_type TEXT NOT NULL CHECK(job_type IN ('initial','scheduled')),
    status TEXT NOT NULL CHECK(status IN ('pending','running','completed','failed')),
    created_at REAL NOT NULL,
    started_at REAL,
    completed_at REAL,
    error TEXT,
    pages_crawled INTEGER NOT NULL DEFAULT 0,
    pages_selected INTEGER NOT NULL DEFAULT 0,
    warnings TEXT NOT NULL DEFAULT '[]',
    output_changed INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_job_per_site ON jobs(website_id)
    WHERE status IN ('pending','running');
CREATE UNIQUE INDEX IF NOT EXISTS one_running_job ON jobs((1)) WHERE status='running';
CREATE INDEX IF NOT EXISTS pending_jobs ON jobs(status,created_at);
CREATE INDEX IF NOT EXISTS due_websites ON websites(next_check_at);
CREATE TABLE IF NOT EXISTS generations (
    generation_id TEXT PRIMARY KEY,
    website_id TEXT NOT NULL REFERENCES websites(website_id),
    markdown TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    generated_at REAL NOT NULL,
    is_latest INTEGER NOT NULL CHECK(is_latest IN (0,1))
);
CREATE UNIQUE INDEX IF NOT EXISTS one_latest_generation ON generations(website_id)
    WHERE is_latest=1;
CREATE INDEX IF NOT EXISTS generation_history ON generations(website_id,generated_at);
CREATE TABLE IF NOT EXISTS pages (
    website_id TEXT NOT NULL REFERENCES websites(website_id),
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    last_observed_at REAL NOT NULL,
    PRIMARY KEY (website_id,url)
);
"""


class NotFound(Exception):
    pass


def digest(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class Database:
    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()

    @contextmanager
    def connection(self, write=False):
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            if write:
                conn.commit()
        except BaseException:
            if write:
                conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)

    def health(self):
        with self.connection() as conn:
            conn.execute("SELECT 1").fetchone()

    @staticmethod
    def _enqueue(conn, website_id, job_type, now):
        active = conn.execute("SELECT * FROM jobs WHERE website_id=? AND status IN ('pending','running')",
                              (website_id,)).fetchone()
        if active:
            return dict(active)
        job_id = uuid4().hex
        conn.execute("INSERT INTO jobs(job_id,website_id,job_type,status,created_at) VALUES (?,?,?,'pending',?)",
                     (job_id, website_id, job_type, now))
        conn.execute("UPDATE websites SET status='pending' WHERE website_id=?", (website_id,))
        return dict(conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone())

    def submit(self, original_url, normalized_url):
        now = time.time()
        with self.connection(write=True) as conn:
            site = conn.execute("SELECT website_id FROM websites WHERE normalized_url=?", (normalized_url,)).fetchone()
            website_id = site[0] if site else uuid4().hex
            if not site:
                conn.execute("INSERT INTO websites(website_id,original_url,normalized_url,created_at,status) VALUES (?,?,?,?,'pending')",
                             (website_id, original_url, normalized_url, now))
            return self._enqueue(conn, website_id, "initial", now)

    def refresh(self, website_id):
        with self.connection(write=True) as conn:
            if not conn.execute("SELECT 1 FROM websites WHERE website_id=?", (website_id,)).fetchone():
                raise NotFound("Website not found")
            # User-requested jobs use 'initial'; 'scheduled' is reserved for automatic checks.
            return self._enqueue(conn, website_id, "initial", time.time())

    def schedule_due(self, interval, now=None):
        now = time.time() if now is None else now
        with self.connection(write=True) as conn:
            sites = conn.execute("""SELECT website_id FROM websites w WHERE next_check_at<=?
                AND EXISTS (SELECT 1 FROM generations g WHERE g.website_id=w.website_id)
                AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.website_id=w.website_id
                    AND j.status IN ('pending','running'))""", (now,)).fetchall()
            for site in sites:
                self._enqueue(conn, site[0], "scheduled", now)
                conn.execute("UPDATE websites SET next_check_at=? WHERE website_id=?", (now + interval, site[0]))
            return len(sites)

    def recover_running(self):
        """Call only while holding the exclusive worker process lock."""
        with self.connection(write=True) as conn:
            conn.execute("UPDATE websites SET status='pending' WHERE website_id IN (SELECT website_id FROM jobs WHERE status='running')")
            count = conn.execute("UPDATE jobs SET status='pending',started_at=NULL WHERE status='running'").rowcount
            return count

    def claim(self):
        with self.connection(write=True) as conn:
            if conn.execute("SELECT 1 FROM jobs WHERE status='running'").fetchone():
                return None
            job = conn.execute("SELECT * FROM jobs WHERE status='pending' ORDER BY created_at,job_id LIMIT 1").fetchone()
            if job is None:
                return None
            now = time.time()
            conn.execute("UPDATE jobs SET status='running',started_at=? WHERE job_id=?", (now, job['job_id']))
            conn.execute("UPDATE websites SET status='running' WHERE website_id=?", (job['website_id'],))
            return dict(conn.execute("SELECT j.*,w.normalized_url FROM jobs j JOIN websites w USING(website_id) WHERE job_id=?",
                                     (job['job_id'],)).fetchone())

    def complete(self, job_id, generation, interval):
        now = time.time()
        content_hash = digest(generation.markdown)
        with self.connection(write=True) as conn:
            job = conn.execute("SELECT * FROM jobs WHERE job_id=? AND status='running'", (job_id,)).fetchone()
            if job is None:
                raise RuntimeError("Job is no longer running")
            site_id = job['website_id']
            previous = conn.execute("SELECT content_hash FROM generations WHERE website_id=? AND is_latest=1", (site_id,)).fetchone()
            changed = previous is None or previous[0] != content_hash
            if changed:
                conn.execute("UPDATE generations SET is_latest=0 WHERE website_id=? AND is_latest=1", (site_id,))
                conn.execute("INSERT INTO generations VALUES (?,?,?,?,?,1)",
                             (uuid4().hex, site_id, generation.markdown, content_hash, now))
            for page in generation.pages:
                page_hash = digest(json.dumps([page.title, page.description, page.content, page.links], ensure_ascii=False))
                conn.execute("""INSERT INTO pages VALUES (?,?,?,?,?,?) ON CONFLICT(website_id,url)
                    DO UPDATE SET title=excluded.title,description=excluded.description,
                    content_hash=excluded.content_hash,last_observed_at=excluded.last_observed_at""",
                             (site_id, page.url, page.title, page.description, page_hash, now))
            conn.execute("""UPDATE jobs SET status='completed',completed_at=?,pages_crawled=?,pages_selected=?,
                warnings=?,output_changed=? WHERE job_id=?""",
                         (now, len(generation.pages), generation.pages_selected, json.dumps(generation.warnings), changed, job_id))
            conn.execute("""UPDATE websites SET title=?,status='completed',last_crawl_at=?,
                last_successful_generation_at=CASE WHEN ? THEN ? ELSE last_successful_generation_at END,
                next_check_at=? WHERE website_id=?""",
                         (generation.title, now, changed, now, now + interval, site_id))
            return changed

    def fail(self, job_id, error, interval):
        now = time.time()
        with self.connection(write=True) as conn:
            job = conn.execute("SELECT website_id FROM jobs WHERE job_id=? AND status='running'", (job_id,)).fetchone()
            if job is None:
                return
            conn.execute("UPDATE jobs SET status='failed',completed_at=?,error=? WHERE job_id=?", (now, error, job_id))
            conn.execute("UPDATE websites SET status='failed',last_crawl_at=?,next_check_at=? WHERE website_id=?",
                         (now, now + interval, job[0]))

    def get_job(self, job_id):
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise NotFound("Job not found")
            job = dict(row)
            job['warnings'] = json.loads(job['warnings'])
            return job

    def get_website(self, website_id):
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM websites WHERE website_id=?", (website_id,)).fetchone()
            if row is None:
                raise NotFound("Website not found")
            site = dict(row)
            latest = conn.execute("SELECT generation_id,content_hash,generated_at,is_latest FROM generations WHERE website_id=? AND is_latest=1", (website_id,)).fetchone()
            site['latest_generation'] = dict(latest) if latest else None
            return site

    def latest(self, website_id):
        self.get_website(website_id)
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM generations WHERE website_id=? AND is_latest=1", (website_id,)).fetchone()
            if row is None:
                raise NotFound("No generated file is available yet")
            return dict(row)

    def generation(self, website_id, generation_id):
        self.get_website(website_id)
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM generations WHERE website_id=? AND generation_id=?",
                               (website_id, generation_id)).fetchone()
            if row is None:
                raise NotFound("Version not found")
            return dict(row)

    def generations(self, website_id):
        self.get_website(website_id)
        with self.connection() as conn:
            return [dict(row) for row in conn.execute("""SELECT generation_id,content_hash,generated_at,is_latest
                FROM generations WHERE website_id=? ORDER BY generated_at DESC,generation_id""", (website_id,))]
