"""Run with `python -m app.worker` on macOS/Linux.

One exclusive process lock per local database bounds crawl concurrency to one.
An OS-level deadline interrupts even DNS, robots delays, and slow responses.
"""
from contextlib import contextmanager
import fcntl
import logging
import signal
import time

from .crawler.urls import UnsafeURL
from .database import Database
from .generation import generate, GenerationError
from .scheduler import enqueue_due
from .settings import Settings

log = logging.getLogger(__name__)


class JobTimeout(BaseException):
    # Crawler catches Exception for partial failures; the deadline must escape it.
    pass


class WorkerStopped(BaseException):
    pass


class WorkerAlreadyRunning(RuntimeError):
    pass


@contextmanager
def worker_lock(database):
    # Never unlink this file: other processes may already hold an open descriptor.
    with database.path.with_suffix(database.path.suffix + ".worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise WorkerAlreadyRunning("Another worker is already using this database") from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


@contextmanager
def deadline(seconds):
    def expired(signum, frame):
        raise JobTimeout()

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def public_error(exc):
    if isinstance(exc, JobTimeout):
        return "Crawl exceeded the whole-job time limit. Try a smaller crawl limit."
    if isinstance(exc, GenerationError):
        return str(exc)
    if isinstance(exc, UnsafeURL):
        return "Crawl rejected an unsafe URL, DNS destination, or redirect."
    if isinstance(exc, ValueError) and "robots.txt" in str(exc):
        return "Crawl could not establish robots.txt permission or the website disallows crawling."
    return "Crawl failed. Check worker logs for details; the website may be unavailable."


def process_one(database, settings, generator=generate):
    """Claim and process one job. The worker entry point holds worker_lock."""
    job = database.claim()
    if job is None:
        return False
    try:
        with deadline(settings.job_timeout):
            generation = generator(job['normalized_url'], settings.crawl_config())
        database.complete(job['job_id'], generation, settings.check_interval)
    except (Exception, JobTimeout) as exc:
        log.exception("Generation job %s failed", job['job_id'])
        database.fail(job['job_id'], public_error(exc), settings.check_interval)
    return True


def run(settings=None):
    settings = settings or Settings.from_env()
    database = Database(settings.db_path)
    database.initialize()

    def stop(signum, frame):
        raise WorkerStopped()

    old_handlers = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        with worker_lock(database):
            needs_recovery = True
            while True:
                try:
                    if needs_recovery:
                        recovered = database.recover_running()
                        log.info("Worker ready; recovered %s interrupted job(s)", recovered)
                        needs_recovery = False
                    enqueue_due(database, settings.check_interval)
                    if not process_one(database, settings):
                        time.sleep(settings.poll_interval)
                except Exception:
                    # No crawl is still executing here. With the exclusive lock,
                    # it is safe to retry a claim whose persistence was interrupted.
                    log.exception("Worker loop failed; retrying after recovery")
                    needs_recovery = True
                    time.sleep(settings.poll_interval)
    except WorkerStopped:
        log.info("Worker stopped; any interrupted claim will be recovered on restart")
    finally:
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        run()
    except WorkerAlreadyRunning as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
