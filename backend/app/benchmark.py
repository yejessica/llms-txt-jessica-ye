"""Opt-in real-network benchmark; producing a file alone is not a quality pass."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
import re
import subprocess
import sys
import time

from .crawler.urls import normalize_url, in_crawl_scope

DEFAULT_MANIFEST = Path(__file__).resolve().parents[1] / "benchmarks" / "sites.json"


def assess(case, stats, exit_code, log="", review=None, output_sha256=None):
    selected = stats.get("selected_pages", [])
    urls = [page["url"] for page in selected]
    expected = case.get("expected_urls", [])
    found = [url for url in expected if url in urls]
    missing = [url for url in expected if url not in urls]
    duplicate_urls = len(urls) - len(set(urls))
    out_of_scope = [url for url in urls if not in_crawl_scope(
        url, case["url"], case.get("crawl_scope", "directory"))]
    substantial = any(page.get("content_characters", 0) >=
                      case.get("min_content_characters", 120) for page in selected)
    warnings = stats.get("errors", [])
    detail = ("\n".join(warnings) + "\n" + log).lower()
    signals = []
    if "robots.txt disallows" in detail or "robots.txt permission" in detail or "robots.txt returned" in detail:
        signals.append("robots")
    if re.search(r"\b(?:http|returned|status)\s*[:=]?\s*429\b|\b429 too many requests\b|retry-after", detail):
        signals.append("rate_limit")
    if re.search(r"\b(?:http|returned|status)\s*[:=]?\s*(?:401|403)\b|\b(?:401 unauthorized|403 forbidden)\b", detail):
        signals.append("access_denied")
    if any(word in detail for word in ("getaddrinfo", "name or service not known",
                                      "nodename nor servname", "certificate_verify_failed")):
        signals.append("network_or_tls")
    if exit_code == 124:
        signals.append("timeout")
    if exit_code or not selected:
        outcome = "failed"
    elif not substantial:
        outcome = "insufficient_content"
    elif warnings:
        outcome = "partial"
    elif missing or duplicate_urls or out_of_scope:
        outcome = "needs_review"
    else:
        outcome = "candidate"
    automated_pass = outcome == "candidate"
    reviewed = review is not None
    manual_pass = bool(review and review.get("meaningful") is True
                       and review.get("accurate") is True
                       and review.get("irrelevant_urls") == []
                       and output_sha256 and review.get("output_sha256") == output_sha256)
    return dict(
        outcome=outcome, signals=signals, automated_checks_passed=automated_pass,
        quality_pass=automated_pass and manual_pass,
        manual_review_status="passed" if manual_pass else "failed" if reviewed else "pending",
        output_sha256=output_sha256,
        manual_review=review or {"output_sha256": output_sha256, "meaningful": None, "accurate": None,
                                 "irrelevant_urls": None, "notes": ""},
        expected_found=found, expected_missing=missing,
        expected_coverage=len(found) / len(expected) if expected else None,
        duplicate_urls=duplicate_urls, out_of_scope_urls=out_of_scope,
        has_substantial_content=substantial,
    )


def write_report(directory, rows):
    report = dict(created_at=datetime.now(timezone.utc).isoformat(),
                  total=len(rows), outcomes=dict(Counter(row["outcome"] for row in rows)),
                  quality_passes=sum(row["quality_pass"] for row in rows), results=rows)
    (directory / "results.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    lines = ["# Website coverage benchmark", "",
             f"Sites: {len(rows)}. Manually confirmed quality passes: {report['quality_passes']}.",
             "Robots, access, network, and rate-limit failures remain in the denominator.", "",
             "A candidate passed automated checks only; read its llms.txt before approving quality.", "",
             "| Site | Type | Outcome | Expected links | Crawled / selected | Duplicates removed | Seconds | Signals | Manual review |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for row in rows:
        stats = row["stats"]
        coverage = f"{len(row['expected_found'])}/{len(row['expected_found']) + len(row['expected_missing'])}"
        cells = [f"[{row['name']}]({row['url']})", row["type"], row["outcome"], coverage,
                 f"{stats.get('crawled', 0)} / {stats.get('selected', 0)}",
                 str(stats.get("duplicates_removed", 0)), str(row["seconds"]),
                 ", ".join(row["signals"]) or "—", row["manual_review_status"]]
        lines.append("| " + " | ".join(cell.replace("|", "\\|").replace("\n", " ") for cell in cells) + " |")
    (directory / "report.md").write_text("\n".join(lines) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=Path("output/benchmarks"))
    parser.add_argument("--only", action="append", help="Site name; repeat to select several")
    parser.add_argument("--max-pages", type=int, default=30)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--reviews", type=Path, help="JSON object keyed by site name; see benchmark README")
    args = parser.parse_args(argv)
    if not 1 <= args.max_pages <= 1000 or not 0 < args.timeout <= 3600:
        parser.error("max-pages must be 1–1000 and timeout must be >0 and <=3600 seconds")
    cases = json.loads(args.manifest.read_text())
    names = [case["name"] for case in cases]
    if len(names) != len(set(names)) or any(not re.fullmatch(r"[a-z0-9-]+", name) for name in names):
        parser.error("Manifest names must be unique lowercase letters, digits, and hyphens")
    if args.only:
        unknown = set(args.only) - set(names)
        if unknown:
            parser.error("Unknown site names: " + ", ".join(sorted(unknown)))
        cases = [case for case in cases if case["name"] in args.only]
    if not cases:
        parser.error("No benchmark cases selected")
    reviews = json.loads(args.reviews.read_text()) if args.reviews else {}
    directory = args.output / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_text(json.dumps(cases, indent=2) + "\n")
    rows = []
    for case in cases:
        normalize_url(case["url"])
        target = directory / f"{case['name']}-llms.txt"
        command = [sys.executable, "-m", "app.cli", "generate", case["url"],
                   "--max-pages", str(args.max_pages), "--max-depth", "3",
                   "--crawl-scope", case.get("crawl_scope", "directory"),
                   "--output", str(target), "--verbose"]
        started = time.monotonic()
        try:
            run = subprocess.run(command, capture_output=True, text=True, timeout=args.timeout)
            log, code = run.stdout + "\n" + run.stderr, run.returncode
        except subprocess.TimeoutExpired as exc:
            def decoded(value):
                return value.decode(errors="replace") if isinstance(value, bytes) else value or ""
            log = decoded(exc.stdout) + "\n" + decoded(exc.stderr) + "\nWhole-crawl benchmark timeout."
            code = 124
        if code == 0 and (not target.exists() or not target.stat().st_size):
            code = 1
            log += "\nCLI produced no llms.txt output."
        (directory / f"{case['name']}.log").write_text(log)
        stats_path = target.with_suffix(".stats.json")
        stats = json.loads(stats_path.read_text()) if code == 0 and stats_path.exists() else {}
        output_sha256 = hashlib.sha256(target.read_bytes()).hexdigest() if code == 0 and target.exists() else None
        row = dict(name=case["name"], type=case["type"], url=case["url"], command=command,
                   exit_code=code, seconds=round(time.monotonic() - started, 2),
                   stats=stats, **assess(case, stats, code, log, reviews.get(case["name"]), output_sha256))
        rows.append(row)
        write_report(directory, rows)
        print(f"{case['name']}: {row['outcome']} ({row['seconds']}s)", flush=True)
    print(f"Report: {directory / 'report.md'}", flush=True)
    return 0 if all(row["automated_checks_passed"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
