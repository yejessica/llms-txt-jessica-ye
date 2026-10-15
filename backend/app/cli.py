import argparse
import json
import logging
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit
from .config import CrawlConfig
from .crawler.crawler import Crawler
from .analysis.ranking import select_pages
from .generator.renderer import render
from .generator.validator import validate

def main(argv=None):
    parser=argparse.ArgumentParser(description="Generate llms.txt from a public website without an LLM")
    sub=parser.add_subparsers(dest="command",required=True)
    generate=sub.add_parser("generate")
    generate.add_argument("url")
    generate.add_argument("--max-pages",type=int,default=40)
    generate.add_argument("--max-depth",type=int,default=3)
    generate.add_argument("--delay",type=float,default=1.0,help="Minimum seconds between requests (default: 1)")
    generate.add_argument("--output",type=Path)
    generate.add_argument("--verbose",action="store_true")
    args=parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,format="%(message)s")
    started=time.monotonic()
    try:
        from .crawler.extractor import trafilatura
        if trafilatura is None:
            print("Warning: Trafilatura unavailable; using BeautifulSoup fallback extraction.",file=sys.stderr)
        config=CrawlConfig(max_pages=args.max_pages,max_depth=args.max_depth,delay=args.delay)
        print(f"Starting crawl: {args.url}\n[1/5] Validating URL...",flush=True)
        result=Crawler(config).crawl(args.url)
        if not result.pages: raise ValueError("No accessible HTML pages were extracted")
        print("[4/5] Ranking pages...",flush=True)
        selected=select_pages(result)
        print("[5/5] Generating llms.txt...",flush=True)
        markdown=render(result.start,result.pages,selected)
        errors=validate(markdown,result.start,result.pages,selected)
        if errors: raise ValueError("; ".join(errors))
        output=args.output or Path("output")/f"{urlsplit(result.start).hostname}-llms.txt"
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(markdown,encoding="utf-8")
        stats=dict(website=result.start,status="partial" if result.errors else "complete",discovered=len(result.discovered),crawled=len(result.pages),selected=len(selected),attempted=result.attempted,requests=result.requests,blocked=result.blocked,seconds=round(time.monotonic()-started,2),errors=result.errors)
        output.with_suffix(".stats.json").write_text(json.dumps(stats,indent=2)+"\n")
        print(f"Successfully generated: {output}\nStatistics:\n"+json.dumps(stats,indent=2))
        if result.errors:
            print(f"Warning: partial crawl; {len(result.errors)} issue(s). Review errors in the statistics.",file=sys.stderr)
        return 0
    except (ValueError,OSError) as exc:
        print(f"Error: {exc}",file=sys.stderr)
        return 1
    except Exception as exc:
        if args.verbose: logging.exception("Crawl failed")
        else: print(f"Error: {exc}",file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
