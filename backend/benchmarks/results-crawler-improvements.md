# Crawl priorities, streaming sitemaps, and URL compatibility

## Comparable full run

Command from `backend/`: `python -m app.benchmark --max-pages 20 --timeout 90`.
This is local public-network crawler validation, not deployment verification.
The full run used the original 12-site expectations, a 20-page budget, depth 3,
and a 90-second timeout per site. The baseline used the same budgets. Sites can
change between runs, so this is a useful comparison, not a controlled experiment.

**Original expectations: 7 → 11 automated candidates; partial results: 2 → 0.**
The remaining exact-URL mismatch was NASA (explained below). All manual content
quality reviews remain pending; candidates are not confirmed quality passes.

| Case | Baseline | New result | Expected links |
| --- | --- | --- | --- |
| example | candidate | candidate | 1/1 |
| python-docs | candidate | candidate | 3/3 |
| python-french | candidate | candidate | 2/2 |
| django | candidate | candidate | 2/2 |
| paul-graham | needs_review | candidate | 1/1 |
| wordpress | candidate | candidate | 2/2 |
| books | needs_review | candidate | 1/1 |
| quotes | partial | candidate | 1/1 |
| gov-uk | partial | candidate | 1/1 |
| nasa | needs_review | needs_review | 0/1 |
| julia-evans | candidate | candidate | 1/1 |
| diagrams | candidate | candidate | 1/1 |

The [full JSON snapshot](results-crawler-improvements.json) preserves selected
URLs, titles, content lengths, warnings, timings, output hashes, and the
original expectation for each case. Raw outputs and logs remain in the
Git-ignored local directory `backend/output/benchmarks/20261016T150921.424598Z/`.

## What improved

- Python documentation retained its tutorial, library, and reference overviews
  while remaining inside the submitted language/version directory.
- Paul Graham now includes the essay index, with legacy image-map navigation
  supported and its homepage duplicate still collapsed.
- Books to Scrape includes the expected product within 20 attempts instead of
  spending the available requests only on category navigation.
- Quotes to Scrape includes the expected author page. Same-site HTTP redirect
  locations are attempted over HTTPS; no plaintext downgrade is sent.
- GOV.UK includes its expected benefits link without the earlier oversized
  sitemap warnings. Large maps are streamed and not kept in the HTTP cache.
- NASA now covers several top-level areas instead of mostly aeronautics
  sitemap articles, and includes a missions overview at its current URL.

## NASA expectation correction

The original expectation was `https://www.nasa.gov/missions/`. The full run
selected `https://www.nasa.gov/nasa-missions/` with the title “Missions - NASA”.
Inspection of the [current NASA homepage](https://www.nasa.gov/) confirmed
that its Missions navigation links to the
[nasa-missions page](https://www.nasa.gov/nasa-missions/).
The checked-in manifest now uses that navigation URL. The original 11/12
full-run result and old expected URL remain preserved above and in JSON.

Focused verification passed: `python -m app.benchmark --only nasa --max-pages 20 --timeout 90`
produced an automated candidate in 34.74 seconds. The
[focused JSON snapshot](results-crawler-improvements-nasa.json) preserves the
corrected expectation and fetched URLs; raw artifacts remain locally in
`backend/output/benchmarks/20261016T151557.970393Z/`. This focused rerun does not
replace or relabel the original full-run snapshot.

## Remaining limits

- No JavaScript execution. The dynamic app case still produces a basic overview.
- Discovery and output ranking remain heuristic; manual relevance/accuracy
  checks are still required. Increasing coverage does not prove every
  selected page belongs in a curated llms.txt.
- Ordinary pages remain limited to 2 MB. Sitemap discovery has per-file
  50 MiB XML / 10 MiB gzip limits, a 64 MiB aggregate XML budget, 30-second
  discovery budget, 50,000 entries per file, and bounded candidate sampling.
  Reaching limits produces warnings rather than silently claiming complete coverage.
- Only bare/www aliases are included; other subdomains remain excluded.
  Every destination requires public IPs and its own origin robots permission.
- Query crawling is conservative: five variants per hostname/path, eight
  fields, and a 2,048-character discovered URL limit. Sort/session/pagination
  combinations remain filtered. Some legitimate pages can still be missed.

## Validation

146 backend tests and 10 subtests passed; 2 deployment safeguard tests passed.
Regression coverage includes section diversity under a small crawl budget,
late-discovered navigation, XML larger than the ordinary page limit, gzip
and XML limits, slow stream deadlines, malformed-map rollback, query traps,
non-ASCII headers, HTTPS-only redirects, destination robots rules, and public-IP pinning.
