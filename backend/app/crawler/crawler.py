from collections import deque, Counter
from dataclasses import dataclass, field
import logging
import time
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser
from .urls import normalize_url, public_addresses, in_crawl_scope, relevant
from .fetcher import Fetcher, RateLimitError
from .sitemap import discover
from .extractor import extract
from .dedup import deduplicate
from ..config import CrawlConfig

log=logging.getLogger(__name__)
@dataclass
class CrawlResult:
    start: str
    pages: list = field(default_factory=list)
    discovered: set = field(default_factory=set)
    errors: list[str] = field(default_factory=list)
    incoming: Counter = field(default_factory=Counter)
    navigation: Counter = field(default_factory=Counter)
    attempted: int = 0
    blocked: int = 0
    requests: int = 0
    elapsed: float = 0
    duplicates_removed: int = 0

class Crawler:
    def __init__(self, config=None, fetcher=None):
        self.config=config or CrawlConfig()
        self.fetcher=fetcher

    def crawl(self,url):
        started=time.monotonic(); start=normalize_url(url)
        if self.fetcher is None: public_addresses(start)
        result=CrawlResult(start)
        page_allowed=lambda u: in_crawl_scope(u,start,self.config.crawl_scope)
        fetcher=self.fetcher or Fetcher(self.config,start)
        parser=RobotFileParser(); robots_text=""
        p=urlsplit(start); robots_url=urlunsplit((p.scheme,p.netloc,"/robots.txt","",""))
        try:
            try:
                response=fetcher.get(robots_url)
                if response.status_code in (404,410): parser.parse([])
                elif 200 <= response.status_code < 300:
                    robots_text=response.text; parser.parse(robots_text.splitlines())
                else: raise ValueError(f"robots.txt returned {response.status_code}")
            except Exception as exc:
                raise ValueError(f"Cannot establish robots.txt permission: {exc}") from exc
            crawl_delay=parser.crawl_delay(self.config.user_agent) or 0
            rate=parser.request_rate(self.config.user_agent)
            if hasattr(fetcher,"minimum_delay"):
                fetcher.minimum_delay=max(self.config.delay,crawl_delay,rate.seconds/rate.requests if rate and rate.requests else 0)
            can_fetch=lambda u: parser.can_fetch(self.config.user_agent,u)
            if not can_fetch(start): raise ValueError("robots.txt disallows submitted URL")
            log.info("[2/5] Discovering sitemap...")
            sitemap=discover(fetcher,start,robots_text,self.config,can_fetch,result.errors)
            # Discover the start first; sitemap seeds are used only after its BFS
            # frontier so broad sitemap lists cannot starve navigation pages.
            queue=deque([(start,0)]); pending={start}; visited=set(); fetched=set()
            seeds=iter(sorted((u for u in sitemap if relevant(u)),key=lambda u:(len(urlsplit(u).path.split('/')),u)))
            result.discovered.add(start)
            result.discovered.update(u for u in sitemap if relevant(u))
            log.info("[3/5] Crawling website...")
            while result.attempted < self.config.max_pages:
                if not queue:
                    if self.config.max_depth == 0: break
                    seed=next((u for u in seeds if u not in visited and u not in fetched),None)
                    if seed is None: break
                    queue.append((seed,1)); pending.add(seed)
                current,depth=queue.popleft(); pending.discard(current)
                if current in visited or current in fetched: continue
                visited.add(current)
                if not can_fetch(current): result.blocked+=1; continue
                result.attempted+=1
                try:
                    # Check robots permission on every redirect as well.
                    response=fetcher.get(current,allowed=lambda u: page_allowed(u) and can_fetch(u))
                    response.raise_for_status()
                    final=normalize_url(str(response.url))
                    if not page_allowed(final) or not can_fetch(final):
                        raise ValueError("Page redirect outside crawl scope or robots permission")
                    if final in fetched: continue
                    fetched.add(final)
                    visited.add(final)
                    if "text/html" not in response.headers.get("content-type","").lower() and "application/xhtml+xml" not in response.headers.get("content-type","").lower(): continue
                    page=extract(response.text,final,depth,start)
                    page.links=[link for link in page.links if page_allowed(link)]
                    page.nav_links=[link for link in page.nav_links if page_allowed(link)]
                    if not page.content:
                        result.errors.append(f"Page {final}: no extractable text (possibly JavaScript-only)")
                        continue
                    page.last_modified=sitemap.get(current)
                    result.pages.append(page); result.discovered.add(final)
                    result.incoming.update(page.links); result.navigation.update(page.nav_links)
                    log.info("Processed %s/%s: %s",result.attempted,self.config.max_pages,final)
                    for link in page.links:
                        if link not in result.discovered and len(result.discovered)>=self.config.max_discovered: continue
                        result.discovered.add(link)
                        if depth<self.config.max_depth and link not in visited and link not in pending and link not in fetched:
                            queue.append((link,depth+1)); pending.add(link)
                except RateLimitError as exc:
                    result.errors.append(str(exc)); log.warning("%s", exc)
                    break
                except Exception as exc:
                    result.errors.append(f"Page {current}: {exc}"); log.warning("%s",result.errors[-1])
            unique=deduplicate(result.pages,start)
            result.duplicates_removed=len(result.pages)-len(unique)
            result.pages=unique
            result.requests=fetcher.requests
            result.elapsed=time.monotonic()-started
            return result
        finally:
            if self.fetcher is None: fetcher.close()
