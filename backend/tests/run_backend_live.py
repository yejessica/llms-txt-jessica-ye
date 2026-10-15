"""Opt-in real HTTP smoke test; never collected by pytest.

Starts actual Uvicorn and worker processes with an isolated temporary database.
Public crawling uses the unchanged production SSRF checks.
Run from backend: python tests/run_backend_live.py
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

import httpx


ROOT = Path(__file__).resolve().parents[1]


def wait_for(callback, timeout=45):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = callback()
        if result:
            return result
        time.sleep(0.1)
    raise TimeoutError('Timed out waiting for backend')


def stop(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def main():
    output = ROOT / 'output'
    output.mkdir(exist_ok=True)
    report = {'website': 'https://example.com/', 'status': 'failed'}
    api = worker = None
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='llms-live-') as directory, \
            (output / 'phase2-api.log').open('w') as api_log, \
            (output / 'phase2-worker.log').open('w') as worker_log:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        env = {**os.environ, 'LLMS_DB_PATH': str(Path(directory) / 'live.sqlite3'),
               'LLMS_MAX_PAGES': '1', 'LLMS_MAX_DEPTH': '0', 'LLMS_CHECK_INTERVAL': '3',
               'LLMS_POLL_INTERVAL': '0.1', 'LLMS_JOB_TIMEOUT': '30', 'LLMS_REQUEST_DELAY': '1'}

        def start_api():
            return subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.api.main:app',
                                     '--host', '127.0.0.1', '--port', str(port)],
                                    cwd=ROOT, env=env, stdout=api_log, stderr=subprocess.STDOUT)

        try:
            api = start_api()
            worker = subprocess.Popen([sys.executable, '-m', 'app.worker'], cwd=ROOT, env=env,
                                      stdout=worker_log, stderr=subprocess.STDOUT)
            with httpx.Client(base_url=f'http://127.0.0.1:{port}', trust_env=False, timeout=10) as client:
                def healthy():
                    if api.poll() is not None:
                        raise RuntimeError('API exited; see output/phase2-api.log')
                    try:
                        return client.get('/api/health').status_code == 200
                    except httpx.ConnectError:
                        return False
                wait_for(healthy, 10)
                response = client.post('/api/generate', json={'url': report['website']})
                assert response.status_code == 202, response.text
                created = response.json()
                report['submission'] = created

                def completed(job_id):
                    job = client.get('/api/jobs/' + job_id).json()
                    if job['status'] == 'failed':
                        raise RuntimeError(job['error'])
                    return job if job['status'] == 'completed' else None

                report['initial_job'] = wait_for(lambda: completed(created['job_id']))
                website_url = '/api/websites/' + created['website_id']
                markdown = client.get(website_url + '/llms.txt')
                assert markdown.status_code == 200 and '# Example Domain' in markdown.text
                (output / 'phase2-example-llms.txt').write_text(markdown.text)
                download = client.get(website_url + '/download')
                assert download.text == markdown.text and 'attachment' in download.headers['content-disposition']
                refreshed = client.post(website_url + '/refresh').json()
                report['manual_job'] = wait_for(lambda: completed(refreshed['job_id']))

                # Read shared persistence to observe an automatically scheduled job
                # (the public API intentionally does not list all jobs).
                import sqlite3
                def scheduled_done():
                    with sqlite3.connect(env['LLMS_DB_PATH']) as conn:
                        conn.row_factory = sqlite3.Row
                        row = conn.execute("SELECT * FROM jobs WHERE job_type='scheduled' AND status IN ('completed','failed') ORDER BY created_at DESC LIMIT 1").fetchone()
                    if row is None:
                        return None
                    if row['status'] == 'failed':
                        raise RuntimeError(row['error'])
                    return dict(row)
                report['scheduled_job'] = wait_for(scheduled_done)
                versions = client.get(website_url + '/generations').json()
                assert len(versions) == 1, versions
                assert report['manual_job']['output_changed'] is False
                assert report['scheduled_job']['output_changed'] == 0
                stop(api)
                api = start_api()
                wait_for(healthy, 10)
                assert client.get(website_url + '/llms.txt').text == markdown.text
                assert client.get('/api/jobs/' + created['job_id']).json()['status'] == 'completed'
                report.update(status='passed', generations=len(versions), persistence_after_api_restart=True,
                              download_verified=True, automatic_scheduling_verified=True)
        except Exception as exc:
            report['error'] = str(exc)
        finally:
            stop(api)
            stop(worker)
            report['seconds'] = round(time.monotonic() - started, 2)
            (output / 'phase2-live-results.json').write_text(json.dumps(report, indent=2) + '\n')
            print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
