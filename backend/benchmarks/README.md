# Website coverage benchmark

The [October 16 results](results-2026-10-16.md) provide a recorded first run and
known gaps, with a neighboring JSON evidence snapshot.

From `backend/`, with the virtual environment activated:

```sh
python -m app.benchmark --only example --only python-docs
python -m app.benchmark  # all 12 sites; potentially several minutes
```

`python tests/run_live.py` forwards to the same runner. Use `--max-pages 20` to
match the proposed deployment page budget; the default is 30. Each site has a
300-second whole-crawl timeout (`--timeout`), depth 3, and the crawler's normal
robots rules, one-second minimum request interval, and bounded rate-limit retry.
Do not repeatedly rerun throttled sites before their cooldown expires.

The checked-in `sites.json` spans minimal HTML, versioned and multilingual docs,
a software project, legacy personal pages, CMS/product pages, ecommerce and
directory sandboxes, a government portal, institutional news, a blog, and a
JavaScript application. The last is an intentional test of a known limitation;
it is included in the denominator. This small convenience sample is not a
statistical estimate of compatibility across the whole web.

Each case specifies expected key-page URLs and an optional minimum extracted
content length (120 characters by default). Expectations are explicit hypotheses
to review as sites change; redirects or changed navigation may require updating
the manifest. Only change expectations after inspecting the site, not merely to
improve the score. `--manifest path.json` loads a different suite; optional
`crawl_scope` can be `directory` (default) or `site` per case.

Each run creates a new timestamped directory under `output/benchmarks/`, with:

- A copy of the selected manifest, generated llms.txt files, raw CLI logs, and
  stats containing selected URLs, titles, content lengths, and duplicates removed.
- `results.json`: expected-link coverage, missing URLs, duplicate/out-of-scope
  URLs, content gate, warnings, access/network/rate-limit signals, and timings.
- `report.md`: a readable summary, including failed and partial cases.

Outcomes are `failed`, `insufficient_content`, `partial`, `needs_review`, or
`candidate`. A candidate has substantial extracted content, all expected links,
no duplicate or out-of-scope selected URLs, and no crawl warnings. Character
length cannot prove relevance or accuracy. Exit status 0 means all selected
cases passed **automated checks**, not that their output passed quality review.
Exit status 1 means at least one case did not pass those checks.

Inspect each output against its source pages: is the content meaningful, are
titles/descriptions accurate, and are irrelevant pages absent? Record reviews
in a JSON object keyed by case name, using the output hash from `results.json`:

```json
{
  "example": {
    "output_sha256": "COPY_THE_EXACT_HASH_FROM_RESULTS",
    "meaningful": true,
    "accurate": true,
    "irrelevant_urls": [],
    "notes": "Compared the overview with the source homepage."
  }
}
```

Use `--reviews reviews.json` on a subsequent run. A quality pass requires the
automated checks and a positive review matching the exact output bytes. Changed
output needs a new review; old site-name approvals cannot silently carry over.
All reviews start pending. The report does not equate a nonempty file or an exit
code with a quality pass. Artifacts remain Git-ignored under `backend/output/`;
copy reviewed reports to a tracked documentation directory when submitting them.

For before/after comparisons, retain both report directories, use the same
manifest and budgets, and inspect expected-page coverage, duplicates, content,
warnings, and runtime together. Live results vary with site changes and access
policies. Offline regression fixtures in `tests/test_coverage.py` verify gzip
limits, directory boundaries, and deduplication without network variability.
