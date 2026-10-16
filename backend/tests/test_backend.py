"""Offline Phase 2 tests: real temporary SQLite, fake HTTP/crawler responses."""
from concurrent.futures import ThreadPoolExecutor
import signal
import socket
import sqlite3
import time
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from app.api.main import create_app
from app.crawler.crawler import Crawler
from app.config import CrawlConfig
from app.database import Database, digest
from app.generation import build_generation
from app.scheduler import enqueue_due
from app.settings import Settings
from app.worker import (
    WorkerAlreadyRunning, process_one, run, worker_lock,
)
from test_pipeline import FakeFetcher, ROOT, html


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    # No test may accidentally resolve a real hostname.
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args, **kwargs: [(0, 0, 0, '', ('8.8.8.8', 443))])


@pytest.fixture
def settings(tmp_path):
    return Settings(db_path=tmp_path / 'test.sqlite3', check_interval=10, poll_interval=0.05,
                    job_timeout=2, request_delay=0)


@pytest.fixture
def db(settings):
    database = Database(settings.db_path)
    database.initialize()
    return database


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client


def fixture_generation(title='Home'):
    result = Crawler(CrawlConfig(delay=0), FakeFetcher({'/': ('text/html', html(title, ['/about', '/docs/']))})).crawl(ROOT)
    return build_generation(result)


def finish(db, settings, title='Home'):
    return process_one(db, settings, lambda url, config: fixture_generation(title))


def submit(client, url=ROOT):
    response = client.post('/api/generate', json={'url': url})
    assert response.status_code == 202, response.text
    return response.json()


def test_create_retrieve_persist_and_fast_submission(client, db, settings):
    with patch('app.generation.Crawler') as crawler:
        created = submit(client, 'HTTPS://SITE.TEST:443/#fragment')
        crawler.assert_not_called()
    job = client.get('/api/jobs/' + created['job_id']).json()
    assert job['status'] == 'pending'
    assert job['pages_crawled'] == job['pages_selected'] == 0
    assert job['error'] is None
    assert job['started_at'] is None
    assert job['job_type'] == 'initial'
    assert Database(settings.db_path).get_job(created['job_id'])['status'] == 'pending'
    site = client.get('/api/websites/' + created['website_id']).json()
    assert site['normalized_url'] == ROOT
    assert site['original_url'].endswith('#fragment')
    assert site['latest_generation'] is None
    assert client.get('/api/health').json() == {'status': 'ok'}


def test_success_pipeline_and_download(client, db, settings):
    created = submit(client)
    result = Crawler(CrawlConfig(delay=0), FakeFetcher()).crawl(ROOT)
    with patch('app.generation.Crawler') as crawler:
        crawler.return_value.crawl.return_value = result
        assert process_one(db, settings)
        crawler.assert_called_once_with(settings.crawl_config())
        crawler.return_value.crawl.assert_called_once_with(ROOT)
    job = client.get('/api/jobs/' + created['job_id']).json()
    assert job['status'] == 'completed'
    assert job['pages_crawled'] == job['pages_selected'] == 4
    assert job['output_changed'] is True
    assert job['started_at'] and job['completed_at']
    website_url = '/api/websites/' + created['website_id']
    site = client.get(website_url).json()
    assert site['title'] == 'Home'
    assert site['status'] == 'completed'
    assert site['last_crawl_at'] and site['last_successful_generation_at'] and site['next_check_at']
    markdown = client.get(website_url + '/llms.txt')
    assert markdown.status_code == 200
    assert markdown.headers['content-type'].startswith('text/plain')
    assert markdown.text.startswith('# Home')
    download = client.get(website_url + '/download')
    assert download.text == markdown.text
    assert download.headers['content-disposition'] == 'attachment; filename="llms.txt"'
    assert download.headers['x-content-type-options'] == 'nosniff'
    history = client.get(website_url + '/generations').json()
    assert len(history) == 1 and history[0]['is_latest']
    assert history[0]['content_hash'] == digest(markdown.text)
    with db.connection() as conn:
        assert conn.execute('SELECT count(*) FROM pages').fetchone()[0] == 4
        assert conn.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'


def test_failure_is_safe_and_worker_processes_next_job(client, db, settings):
    first = submit(client)
    second = submit(client, 'https://other.test/')
    def fail(url, config):
        raise RuntimeError('secret internal filesystem path /private/secret')
    assert process_one(db, settings, fail)
    job = client.get('/api/jobs/' + first['job_id']).json()
    assert job['status'] == 'failed'
    assert job['error'] and 'secret' not in job['error']
    assert job['completed_at']
    assert finish(db, settings)
    assert db.get_job(second['job_id'])['status'] == 'completed'
    assert db.get_website(first['website_id'])['last_successful_generation_at'] is None


def test_duplicate_submissions_refresh_and_claim_constraints(client, db, settings):
    created = submit(client)
    assert submit(client, ROOT + '#fragment')['job_id'] == created['job_id']
    assert client.post(f"/api/websites/{created['website_id']}/refresh").json()['job_id'] == created['job_id']
    assert db.claim()['job_id'] == created['job_id']
    assert db.claim() is None
    assert submit(client)['status'] == 'running'
    with db.connection(write=True) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO jobs(job_id,website_id,job_type,status,created_at) VALUES ('duplicate',?,'initial','pending',0)", (created['website_id'],))


def test_concurrent_submissions_and_atomic_claim(db):
    with ThreadPoolExecutor(max_workers=8) as executor:
        submitted = list(executor.map(lambda _: db.submit(ROOT, ROOT), range(20)))
    assert len({job['job_id'] for job in submitted}) == 1
    db.submit('https://other.test/', 'https://other.test/')
    with ThreadPoolExecutor(max_workers=8) as executor:
        claimed = list(executor.map(lambda _: db.claim(), range(20)))
    assert sum(job is not None for job in claimed) == 1


def test_manual_refresh_versions_and_identical_output(client, db, settings):
    created = submit(client)
    finish(db, settings)
    website_url = '/api/websites/' + created['website_id']
    first_file = db.latest(created['website_id'])
    first_success = db.get_website(created['website_id'])['last_successful_generation_at']
    refresh = client.post(website_url + '/refresh').json()
    assert refresh['job_id'] != created['job_id']
    assert refresh['status'] == 'pending'
    assert client.get(website_url + '/llms.txt').text == first_file['markdown']
    finish(db, settings)
    assert db.get_job(refresh['job_id'])['output_changed'] == 0
    assert len(db.generations(created['website_id'])) == 1
    assert db.get_website(created['website_id'])['last_successful_generation_at'] == first_success
    assert db.get_website(created['website_id'])['last_crawl_at'] > first_success
    client.post(website_url + '/refresh')
    finish(db, settings, 'Updated Home')
    history = db.generations(created['website_id'])
    assert len(history) == 2
    assert sum(item['is_latest'] for item in history) == 1
    with db.connection() as conn:
        assert conn.execute('SELECT markdown FROM generations WHERE generation_id=?', (first_file['generation_id'],)).fetchone()[0] == first_file['markdown']
    previous = client.get(f"{website_url}/generations/{first_file['generation_id']}")
    assert previous.status_code == 200
    assert previous.headers['content-type'].startswith('text/plain')
    assert previous.text == first_file['markdown']
    assert previous.text != client.get(website_url + '/llms.txt').text


def test_generation_lookup_is_scoped_to_its_website(client, db, settings):
    first = submit(client)
    finish(db, settings, 'First')
    second = submit(client, 'https://other.test/')
    finish(db, settings, 'Second')
    first_version = db.latest(first['website_id'])['generation_id']
    other_site = client.get(f"/api/websites/{second['website_id']}/generations/{first_version}")
    assert other_site.status_code == 404
    assert other_site.json()['error']['code'] == 'not_found'
    assert client.get(f"/api/websites/{first['website_id']}/generations/missing").status_code == 404


def test_multiple_websites_keep_separate_outputs(client, db, settings):
    first = submit(client)
    finish(db, settings, 'First')
    second = submit(client, 'https://other.test/')
    finish(db, settings, 'Second')
    assert db.latest(first['website_id'])['markdown'].startswith('# First')
    assert db.latest(second['website_id'])['markdown'].startswith('# Second')


def test_failed_refresh_preserves_last_file(client, db, settings):
    created = submit(client)
    finish(db, settings)
    previous = db.latest(created['website_id'])
    job = client.post(f"/api/websites/{created['website_id']}/refresh").json()
    with patch('app.generation.Crawler') as crawler:
        crawler.return_value.crawl.side_effect = ValueError('robots.txt disallows submitted URL')
        process_one(db, settings)
    assert db.get_job(job['job_id'])['status'] == 'failed'
    assert 'robots.txt' in db.get_job(job['job_id'])['error']
    assert db.latest(created['website_id']) == previous


def test_scheduled_recrawl_unchanged_and_changed(db, settings):
    created = db.submit(ROOT, ROOT)
    finish(db, settings)
    original = db.get_website(created['website_id'])
    assert enqueue_due(db, 10, now=original['next_check_at'] - 1) == 0
    due = original['next_check_at'] + 1
    assert enqueue_due(db, 10, now=due) == 1
    assert enqueue_due(db, 10, now=due + 100) == 0  # Active job, even if overdue.
    with db.connection() as conn:
        job = dict(conn.execute("SELECT * FROM jobs WHERE status='pending'").fetchone())
    assert job['job_type'] == 'scheduled'
    finish(db, settings)
    assert db.get_job(job['job_id'])['output_changed'] == 0
    assert len(db.generations(created['website_id'])) == 1
    checked = db.get_website(created['website_id'])
    assert checked['last_crawl_at'] > original['last_crawl_at']
    assert checked['next_check_at'] > checked['last_crawl_at']
    assert enqueue_due(db, 10, now=checked['next_check_at']) == 1
    finish(db, settings, 'Changed')
    assert len(db.generations(created['website_id'])) == 2


def test_no_scheduling_before_first_success_and_no_duplicate_manual_job(db, settings):
    created = db.submit(ROOT, ROOT)
    assert enqueue_due(db, 10, now=time.time() + 10000) == 0
    finish(db, settings)
    db.refresh(created['website_id'])
    assert enqueue_due(db, 10, now=time.time() + 10000) == 0


def test_restart_recovery_and_exclusive_worker(db, settings):
    created = db.submit(ROOT, ROOT)
    with worker_lock(db):
        with pytest.raises(WorkerAlreadyRunning), worker_lock(Database(settings.db_path)):
            pass
        assert db.claim()['job_id'] == created['job_id']
    # Simulate a process exiting before completion. OS releases its lock.
    with worker_lock(db):
        assert db.recover_running() == 1
        assert db.get_job(created['job_id'])['started_at'] is None
        finish(db, settings)
    assert db.get_job(created['job_id'])['status'] == 'completed'
    assert len(db.generations(created['website_id'])) == 1


def test_sigterm_during_crawl_recovers_on_restart(db, settings):
    created = db.submit(ROOT, ROOT)
    actual = process_one
    def interrupted(database, config):
        def stop_during_crawl(*args):
            signal.raise_signal(signal.SIGTERM)
        actual(database, config, stop_during_crawl)
    with patch('app.worker.process_one', side_effect=interrupted):
        run(settings)
    assert db.get_job(created['job_id'])['status'] == 'running'
    def complete_after_restart(database, config):
        actual(database, config, lambda *_: fixture_generation())
        signal.raise_signal(signal.SIGTERM)
    with patch('app.worker.process_one', side_effect=complete_after_restart):
        run(settings)
    assert db.get_job(created['job_id'])['status'] == 'completed'
    assert len(db.generations(created['website_id'])) == 1


def test_generation_persistence_rolls_back_atomically(db, settings):
    created = db.submit(ROOT, ROOT)
    finish(db, settings)
    old = db.latest(created['website_id'])
    refresh = db.refresh(created['website_id'])
    db.claim()
    broken = fixture_generation('Changed')
    broken.pages[0].description = None  # Simulate a persistence failure after version insertion.
    with pytest.raises(sqlite3.IntegrityError):
        db.complete(refresh['job_id'], broken, settings.check_interval)
    assert db.latest(created['website_id']) == old
    assert len(db.generations(created['website_id'])) == 1
    assert db.get_job(refresh['job_id'])['status'] == 'running'


def test_page_hash_changes_without_redundant_markdown_version(db, settings):
    created = db.submit(ROOT, ROOT)
    finish(db, settings)
    with db.connection() as conn:
        before = conn.execute('SELECT content_hash FROM pages WHERE url=?', (ROOT,)).fetchone()[0]
    generation = fixture_generation()
    generation.pages[0].content += ' Extra body content not included in Markdown metadata.'
    db.refresh(created['website_id'])
    process_one(db, settings, lambda *_: generation)
    with db.connection() as conn:
        after = conn.execute('SELECT content_hash FROM pages WHERE url=?', (ROOT,)).fetchone()[0]
    assert before != after
    assert len(db.generations(created['website_id'])) == 1


def test_job_deadline_escapes_crawler_exception_handlers(db, settings):
    created = db.submit(ROOT, ROOT)
    fast = settings.model_copy(update={'job_timeout': 0.03})
    def slow(*args):
        try:
            time.sleep(1)
        except Exception:
            pytest.fail('The deadline was swallowed by an Exception handler')
    previous = signal.getsignal(signal.SIGALRM)
    assert process_one(db, fast, slow)
    assert db.get_job(created['job_id'])['status'] == 'failed'
    assert 'time limit' in db.get_job(created['job_id'])['error']
    assert signal.getsignal(signal.SIGALRM) == previous
    assert signal.getitimer(signal.ITIMER_REAL)[0] == 0


def test_worker_loop_automatically_schedules_and_stops(db, settings):
    created = db.submit(ROOT, ROOT)
    finish(db, settings)
    with db.connection(write=True) as conn:
        conn.execute('UPDATE websites SET next_check_at=0')
    actual = process_one
    def process_and_stop(database, config):
        assert actual(database, config, lambda *_: fixture_generation('Automatic'))
        signal.raise_signal(signal.SIGTERM)
    with patch('app.worker.process_one', side_effect=process_and_stop):
        run(settings)
    assert len(db.generations(created['website_id'])) == 2
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) FROM jobs WHERE job_type='scheduled' AND status='completed'").fetchone()[0] == 1


def test_empty_crawl_error(db, settings):
    created = db.submit(ROOT, ROOT)
    with patch('app.generation.Crawler') as crawler:
        crawler.return_value.crawl.return_value.pages = []
        process_one(db, settings)
    assert db.get_job(created['job_id'])['error'] == 'No accessible HTML pages were extracted.'


def test_partial_crawl_retains_warnings(db, settings):
    created = db.submit(ROOT, ROOT)
    result = Crawler(CrawlConfig(delay=0), FakeFetcher({'/about': TimeoutError('timeout')})).crawl(ROOT)
    process_one(db, settings, lambda *_: build_generation(result))
    job = db.get_job(created['job_id'])
    assert job['status'] == 'completed' and job['warnings']
    assert job['pages_crawled'] == 3


@pytest.mark.parametrize('url', [
    'ftp://example.com', 'file:///etc/passwd', 'not a url', 'https://u:p@example.com/',
    'https://example.com:8080/', 'https://localhost/', 'https://a.local/',
    'https://example.com/\\evil', 'https://example.com/\nfoo',
])
def test_url_validation(client, url):
    assert client.post('/api/generate', json={'url': url}).status_code == 422


@pytest.mark.parametrize('ip', ['127.0.0.1', '10.0.0.1', '169.254.169.254', '::1', '::ffff:8.8.8.8'])
def test_non_public_addresses_rejected(client, monkeypatch, ip):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args, **kwargs: [(0, 0, 0, '', (ip, 443))])
    response = client.post('/api/generate', json={'url': 'https://example.com'})
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'invalid_url'


def test_dns_failure_rejected(client, monkeypatch):
    def failed(*args, **kwargs):
        raise socket.gaierror('internal DNS failure')
    monkeypatch.setattr(socket, 'getaddrinfo', failed)
    response = client.post('/api/generate', json={'url': ROOT})
    assert response.status_code == 422 and 'internal DNS' not in response.text


@pytest.mark.parametrize('body', [{}, {'url': 123}, {'url': ''}, {'url': 'x' * 2049}, {'url': ROOT, 'extra': 1}])
def test_request_validation(client, body):
    response = client.post('/api/generate', json=body)
    assert response.status_code == 422
    assert 'error' in response.json()


def test_missing_resources_and_file_not_ready(client):
    for url in ['/api/jobs/missing', '/api/websites/missing', '/api/websites/missing/llms.txt',
                '/api/websites/missing/download', '/api/websites/missing/generations']:
        response = client.get(url)
        assert response.status_code == 404
        assert response.json()['error']['code'] == 'not_found'
    assert client.post('/api/websites/missing/refresh').status_code == 404
    created = submit(client)
    assert client.get(f"/api/websites/{created['website_id']}/llms.txt").status_code == 404


def test_database_error_response(client):
    with patch.object(client.app.state.database, 'health', side_effect=sqlite3.OperationalError('secret path')):
        response = client.get('/api/health')
    assert response.status_code == 503
    assert 'secret path' not in response.text


def test_settings_environment_and_validation(monkeypatch, tmp_path):
    monkeypatch.setenv('LLMS_DB_PATH', str(tmp_path / 'env.sqlite3'))
    monkeypatch.setenv('LLMS_CHECK_INTERVAL', '3')
    assert Settings.from_env().check_interval == 3
    assert Settings.from_env().db_path == tmp_path / 'env.sqlite3'
    for field in ['check_interval', 'poll_interval', 'job_timeout', 'request_timeout']:
        with pytest.raises(ValueError):
            Settings(**{field: 0})
        with pytest.raises(ValueError):
            Settings(**{field: float('nan')})
