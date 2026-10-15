# Automated llms.txt Generator — Phase 2

Deterministic Python crawler and CLI, now with a FastAPI backend, SQLite
persistence, a separate serial worker, and automatic scheduled recrawls. No LLM
key, Docker, Redis, external database, or frontend is required.

**Status:** 89 pytest tests and 10 subtests pass, including all 48 Phase 1 tests.
A real `example.com` end-to-end run passed on October 15, 2026: initial generation,
download, manual refresh, automatic recrawl, unchanged-output deduplication, and
persistence across an API restart. Phase 1 selection-quality limitations remain;
the five-site report is in the local `TESTING.md` if available.

## Run the backend locally

Python 3.11+ on macOS or Ubuntu. From the repository root:

```sh
cd backend
python3 -m venv .venv  # skip if the existing environment is already set up
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env  # optional; do not overwrite an existing customized .env
set -a
source .env
set +a
uvicorn app.api.main:app --reload
```

In a second terminal, from the repository root:

```sh
cd backend
source .venv/bin/activate
set -a
source .env
set +a
python -m app.worker
```

Environment loading is explicit; `.env` is not automatically read. If you skip
creating `.env`, skip the `source .env` lines in both terminals. Both processes
must use the same database path. The default is an absolute path to
`backend/data/llms.sqlite3`; the example environment uses `./data/llms.sqlite3`
relative to the process working directory. Tables and WAL mode initialize
automatically on startup. Database and local environment files are gitignored.

The scheduler runs inside the worker, automatically checking for due websites
before each job claim. **No separate scheduler process or command is needed.**
Keep the worker running for both generation and automatic updates. The API alone
only persists queued jobs. API documentation: <http://127.0.0.1:8000/docs>.

```sh
curl -X POST http://127.0.0.1:8000/api/generate \
  -H 'Content-Type: application/json' -d '{"url":"https://example.com"}'
# Copy job_id and website_id from the response into the commands below.
curl http://127.0.0.1:8000/api/jobs/JOB_ID
curl http://127.0.0.1:8000/api/websites/WEBSITE_ID
curl http://127.0.0.1:8000/api/websites/WEBSITE_ID/llms.txt
curl -OJ http://127.0.0.1:8000/api/websites/WEBSITE_ID/download
curl -X POST http://127.0.0.1:8000/api/websites/WEBSITE_ID/refresh
curl http://127.0.0.1:8000/api/websites/WEBSITE_ID/generations
```

### API contract

| Method | Endpoint | Behavior |
| --- | --- | --- |
| POST | `/api/generate` | Validate public URL, persist website/job, return HTTP 202 with `job_id`, `website_id`, `status` |
| GET | `/api/jobs/{job_id}` | Job type/status, UTC timestamps, counts, error, warnings, `output_changed` |
| GET | `/api/websites/{website_id}` | Original/normalized URL, title, status, timestamps, latest generation metadata |
| GET | `/api/websites/{website_id}/llms.txt` | Latest Markdown as UTF-8 plain text |
| GET | `/api/websites/{website_id}/download` | Same content with `Content-Disposition: attachment; filename="llms.txt"` |
| POST | `/api/websites/{website_id}/refresh` | Queue a manual recrawl; HTTP 202 |
| GET | `/api/websites/{website_id}/generations` | Generation metadata, newest first; prior Markdown remains in SQLite |
| GET | `/api/health` | Basic API/database health: `{"status":"ok"}` |

Errors consistently use `{"error":{"code":"...","message":"..."}}`.
Missing resources or a file that is not ready return 404; invalid input or unsafe
URLs return 422; database unavailability returns 503; unexpected API failures
return a generic 500. Detailed exceptions are logged on the server, without
tracebacks in API responses. A crawl failure is recorded on the job, not returned
as an HTTP failure from the original submission.

Submitting an already active normalized URL or refreshing an active website
returns the existing job rather than adding a duplicate. Submitting a completed
website queues a new user-requested job. User-requested jobs (including manual
refreshes) use `job_type: "initial"`; automatic checks use `"scheduled"`.

### Persistence and processing

The minimal schema lives in `app/database.py` and uses built-in `sqlite3`:

| Table | Stored data |
| --- | --- |
| `websites` | ID, original and unique normalized URL, title, creation/last crawl/last stored generation/next check timestamps, status |
| `jobs` | ID, website FK, type/status, creation/start/completion timestamps, safe error, extracted/selected page counts, warnings, whether output changed |
| `generations` | ID, website FK, complete Markdown, SHA-256, timestamp, latest flag |
| `pages` | Website FK and URL, title, description, SHA-256 of extracted metadata/text/links, last observed timestamp |

Write transitions use short `BEGIN IMMEDIATE` transactions, foreign keys, a
10-second SQLite busy timeout, and WAL mode. Unique partial indexes enforce one
active job per website, one running job globally, and one latest file per website.
Generation insertion, page metadata, website state, and job completion commit
atomically. Historical Markdown is retained. A failed refresh leaves the last
successful file downloadable.

The API never crawls. `app/generation.py` shares the existing
`Crawler(CrawlConfig).crawl` → `select_pages` → `render` → `validate` sequence
with the CLI. The crawler, ranking rules, redirects, IP pinning, and robots policy
are unchanged. Submission performs initial URL/DNS validation; execution checks
DNS again and validates every redirect, preventing rebinding from bypassing the
fetcher's public-IP checks. Submission waits for DNS validation, not the crawl.

`python -m app.worker` exclusively locks a sibling `.worker.lock` file for its
lifetime and claims jobs atomically. A second worker for the same local database
exits with a clear error. At startup, while holding this lock, it requeues stale
running jobs from an interrupted process. An interrupted crawl may be retried;
committed generations are protected by atomic completion and hash comparison.
SIGINT/SIGTERM stop the worker and leave an interrupted claim for restart recovery.
The single main-thread worker uses Unix signals for the whole-job deadline;
macOS/Linux are supported. Individual crawl failures are recorded and the worker
continues; temporary persistence failures cause a logged recovery/retry loop.

Once a website has a generation, it is automatically recrawled when due. Each
check runs a bounded full crawl; it is not an incremental crawler. A different
Markdown SHA-256 creates a new generation and moves the latest flag. Identical
output completes the job with `output_changed: false` and updates `last_crawl_at`
and `next_check_at`, without creating a version. `last_successful_generation_at`
changes only when a new version is stored. Body-only changes can update page
hashes without changing the rendered file. Previously observed page records are
retained; their observation timestamps identify the last time each was seen.

The next check is scheduled from completion (including failure), avoiding hot
retry loops. Due checks also queue on worker startup after downtime. Failed
initial submissions without a stored generation require a manual retry; failed
refreshes of previously generated sites remain eligible for automatic checks.

### Configuration

All intervals and timeouts are seconds. Copy `backend/.env.example` to `.env`
and load it into both processes, or export variables directly.

| Variable | Default | Purpose |
| --- | --- | --- |
| `LLMS_DB_PATH` | `backend/data/llms.sqlite3` (absolute default) | Shared SQLite file |
| `LLMS_CHECK_INTERVAL` | `86400` | Time between checks; set to `10` for a local demo (minimum 1) |
| `LLMS_POLL_INTERVAL` | `2` | Idle queue/scheduler polling interval (minimum 0.05) |
| `LLMS_JOB_TIMEOUT` | `300` | Whole crawl/generation deadline |
| `LLMS_MAX_PAGES` | `40` | Attempted pages, range 1–1,000 |
| `LLMS_MAX_DEPTH` | `3` | Discovery depth, range 0–20 |
| `LLMS_REQUEST_TIMEOUT` | `15` | Per-network-operation timeout |
| `LLMS_REQUEST_DELAY` | `1` | Minimum request interval; robots policy may increase it |

The existing `CrawlConfig` still supplies response-size, sitemap, discovery,
retry, and user-agent limits. CLI flags continue to configure standalone CLI
crawls; backend environment variables configure worker crawls.

### Phase 2 verification and limitations

```sh
cd backend
source .venv/bin/activate
python -m pytest -q
python tests/run_backend_live.py  # optional; requires public internet and local sockets
```

The offline suite uses temporary SQLite files and mocked HTTP/crawler results.
It covers creation/status, success/failure, persistence, concurrent submission
and claiming, active-job uniqueness, multiple websites, manual refreshes,
scheduling, output/page hash changes, unchanged output, atomic rollback, worker
locking, shutdown/restart recovery, deadlines, downloads, and URL validation.
The installed Starlette version emits one non-failing TestClient/httpx
deprecation warning; no additional HTTP client dependency is needed at runtime.

The optional live test starts real API/worker subprocesses against an isolated
temporary SQLite database, checks `example.com` with one-page/depth-zero limits,
downloads its output, refreshes it, observes an automatic check, and restarts the
API to verify persistence. It cleans up its processes/database. The recorded run
passed in **10.82 seconds**, with three completed jobs and **one generation**.
Logs, JSON results, and the generated file are saved under gitignored
`backend/output/phase2-*`. Changes to a real website were not simulated; changed
output is covered by deterministic offline tests.

Operational limits to revisit before Phase 3:

- One local worker and SQLite file on macOS/Linux; no Windows or shared/network
  filesystem support. Scheduling waits for an active crawl to finish, and queued
  jobs may take longer than the configured check interval to start.
- Health reports API/database availability, not worker liveness or queue latency.
- Bounded full recrawls, no conditional HTTP/incremental fetching. Page hashes
  are observational metadata; the Markdown hash controls version creation.
- As in Phase 1, useful partial crawls can complete with warnings. Temporary page
  failures may produce a smaller new file; clients should inspect job warnings.
- No retention/pagination, schema migration framework, or dependency lockfile yet.
  These are follow-ups if history or the schema grows.
- No authentication or per-client abuse controls in this phase. Decide access,
  quotas, and frontend origin configuration before exposing a public service.
  No deployment or Phase 3 implementation has been performed.

## Standalone CLI

Python 3.11+ on macOS or Ubuntu:

```sh
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m app.cli generate https://example.com
```

The repository itself is the project root; it is not nested inside another
`llms-txt-generator` directory. Run the CLI from `backend` so `app` is importable.

```sh
python -m app.cli generate https://docs.python.org/3/ \
  --max-pages 30 \
  --max-depth 3 \
  --output ./output/python-llms.txt \
  --verbose
```

Default limits: 40 attempted pages, depth 3, 25 selected pages, one concurrent
request, 1-second minimum interval, 15-second per-operation timeout, 2 MB per
response, 12 sitemap documents, and 5,000 discovered URLs. CLI ranges are 1–1,000
pages and depth 0–20. Other settings live in `app/config.py` and can be supplied
through `CrawlConfig` when importing the crawler. Use `--delay 2` to request a
slower two-second interval. The request interval increases
to respect robots.txt crawl delay/request rate. An attempt can fail or return a
non-HTML document, so extracted page counts can be lower than the page limit.

Outputs include Markdown and a neighboring `.stats.json` file with actual counts,
request totals, errors, and elapsed time. Exit codes: 0 success, 1 crawl or output
failure, 2 invalid command arguments. A useful output from a partial crawl still
returns 0, with `status: "partial"` and a warning; complete crawls report
`status: "complete"`. Neither status proves selection quality. Failed page requests do not abort a crawl;
no accessible HTML pages is an error. Verbose mode logs individual page progress.

## Tests

```sh
cd backend
source .venv/bin/activate
python -m pytest -q
python tests/run_live.py
```

The Phase 1 tests use `unittest.TestCase`, which pytest discovers normally. Those
tests also run via `python -m unittest discover -s tests -v`; run pytest for the
complete Phase 1 and Phase 2 suite. Unit tests use in-memory HTTP fixtures and
never contact public sites. The Phase 1 tests cover URL security,
normalization, sitemaps, BFS, deduplication, extraction, ranking, rendering,
validation, CLI output, errors, limits, and redirects. The live runner invokes the
real CLI on five websites with 30-page limits, retaining logs and JSON outcomes.
A successful live runner process only means attempts were recorded; inspect each
row's exit code and manually review generated Markdown before assigning Pass.

## How it works

1. **Safe fetching:** normalize HTTP(S) URLs, reject credentials and nonstandard
   ports, resolve every new connection, and reject any DNS answer set containing
   a non-public IP. Requests connect to the checked IP directly while retaining
   the hostname for Host, TLS verification, and SNI. Environment proxies are not
   used. Redirects are manual, limited, scoped, and checked against robots rules.
   Responses are bounded and cached within a crawl to avoid fetching redirect
   targets twice. Canonical metadata cannot initiate an unsafe request.
2. **Sitemaps:** fetch origin robots.txt, read declared same-origin sitemaps, and
   check `/sitemap.xml`. Parse namespaced URL sets and nested indexes, preserve
   `lastmod` hints, filter scope, and bound sitemap traversal. XML entity/DOCTYPE
   declarations are rejected. Robots permission is checked for sitemap fetches.
3. **BFS:** crawl the submitted page and its internal links breadth first using
   pending/visited sets. When the linked frontier is exhausted, remaining sitemap
   URLs supplement discovery as depth-one seeds. Depth zero never uses seeds.
   Depth is discovery distance, not path segment count; a sitemap seed's true
   link distance is unknown. Discovered includes eligible links found beyond the
   crawl depth, and may include robots-blocked pages; only fetched HTML is output.
4. **Extraction:** BeautifulSoup extracts titles, meta descriptions, headings,
   scoped links, and navigation links. Trafilatura extracts primary text. Cleaned
   BeautifulSoup text is the fallback if Trafilatura finds no content; if the
   package is missing entirely, the CLI warns and uses this degraded mode.
   Descriptions use extracted metadata or a visible paragraph, shortened without
   paraphrasing. Canonicals are preferred only if the canonical page was actually
   fetched within scope and its extracted content is identical. Empty client-side
   shells are reported rather than populated with invented descriptions.
5. **Ranking:** score pages using discovery depth, incoming/nav link frequency,
   title/description availability, content length, overview/documentation hints,
   and the starting page. Select up to 25 pages by round-robin across URL sections,
   then group them into readable Markdown sections. Scoring and selection are
   separate from crawling and can be replaced later.
6. **Rendering:** emit one H1, an optional grounded summary blockquote, and H2
   file lists with escaped Markdown labels and descriptions. The validator checks
   exact deterministic rendering, H1 structure, URL validity, uniqueness, and
   membership/metadata consistency against fetched pages.

The renderer follows the [llms.txt format](https://llmstxt.org/#format): only H1 is
required; summaries and H2 file lists are optional. This is validation of the
emitted subset, not a general Markdown parser or proof that a website's metadata
is truthful. Links use fetched HTML URLs; Markdown variants are never guessed.

The reusable entry point is `Crawler(CrawlConfig(...)).crawl(url)`. It returns
extracted pages, link signals, counts, and errors without writing files. Ranking,
rendering, and validation are independent Python functions, shared through
`app/generation.py`. The CLI writes local artifacts; the worker stores outputs
in SQLite.

## Engineering trade-offs and limits

- Serial requests keep politeness, ordering, and failure handling simple. A large
  site can take minutes; CLI timeouts apply to individual network operations.
  The Phase 2 worker additionally imposes a whole-job deadline.
- Scope is the exact hostname and effective origin, permitting HTTP to HTTPS
  upgrades. `www` aliases, subdomains, and cross-origin sitemap hosting are not
  followed automatically. Submit the final public hostname.
- Missing robots.txt (404/410) allows crawling; other errors fail closed. The
  crawler never bypasses denied rules. Robots rules and delays are a site policy,
  not authorization to access authenticated content.
- Directory trailing slashes are preserved because `/docs/` and `/docs` can have
  different relative-link semantics. Repeated slashes and fragments normalize;
  redirect caching removes server-declared aliases. Other distinct URLs with
  identical content are not merged without a verified canonical.
- Query-bearing discovered links, common auth/search/cart routes, pagination, and
  non-HTML asset extensions are filtered conservatively. This loses some useful
  query-based pages. Ranking is heuristic and not semantically aware.
- No JavaScript execution, browser automation, compressed sitemap files, general retries,
  persistent cache, incremental updates, or LLM summaries. Thin server HTML can
  produce thin output even when a browser shows substantial content.
- Real-world selection still needs review. The Python snapshot includes many
  alternate documentation versions, and Paul Graham includes both root and
  index.html. Dependency ranges are constrained but not a reproducible lockfile.

Phase 2 is implemented above. Development stops here pending Phase 3 approval.

On HTTP 429 the fetcher reads Retry-After (seconds or HTTP date), waits and retries
once when the requested wait is at most 30 seconds (five seconds without a valid
header), and raises its subsequent interval to at least two seconds. A longer
wait or a second 429 stops the crawl without making requests to other pages.
Already extracted pages can still generate a partial file. No 429 is cached.
If sitemap discovery or robots.txt is rate-limited before page extraction, the
CLI fails rather than continuing against the throttled site. Wait until the
site's requested cooldown expires before starting another run.
