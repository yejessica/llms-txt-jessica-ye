# Automated llms.txt Generator — Phase 1

Standalone, deterministic Python crawler and CLI. No LLM key, frontend, API server,
database, worker, scheduler, or deployment infrastructure is required.

**Status:** 48 tests pass with Trafilatura and pytest installed. User-run live
crawls produced all five outputs; Django encountered throttling. Rate-limit
handling is now corrected and needs a live rerun. Selection-quality review
remains open; see [TESTING.md](TESTING.md).

## Install and run

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

The test suite uses `unittest.TestCase`, which pytest discovers normally. It also
runs without pytest via `python -m unittest discover -s tests -v`. Unit tests use
in-memory HTTP fixtures and never contact public sites. They cover URL security,
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
rendering, and validation are independent Python functions. Only the CLI writes
artifacts, allowing future FastAPI/worker reuse.

## Engineering trade-offs and limits

- Serial requests keep politeness, ordering, and failure handling simple. A large
  site can take minutes; timeouts apply to individual network operations, not a
  whole job. The future backend should impose a job deadline.
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

Phase 2 is intentionally unimplemented and requires approval after Phase 1 review.

On HTTP 429 the fetcher reads Retry-After (seconds or HTTP date), waits and retries
once when the requested wait is at most 30 seconds (five seconds without a valid
header), and raises its subsequent interval to at least two seconds. A longer
wait or a second 429 stops the crawl without making requests to other pages.
Already extracted pages can still generate a partial file. No 429 is cached.
If sitemap discovery or robots.txt is rate-limited before page extraction, the
CLI fails rather than continuing against the throttled site. Wait until the
site's requested cooldown expires before starting another run.
