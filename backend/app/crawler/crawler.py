from collections import Counter
from dataclasses import dataclass, field
import logging
import time

from .urls import normalize_url, public_addresses, in_crawl_scope, site_url, relevant
from .fetcher import Fetcher, RateLimitError
from .sitemap import discover
from .extractor import extract
from .dedup import deduplicate
from .frontier import Frontier
from .policy import CrawlPolicy
from ..config import CrawlConfig

log = logging.getLogger(__name__)


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
        self.config = config or CrawlConfig()
        self.fetcher = fetcher

    def crawl(self, url):
        started = time.monotonic()
        start = normalize_url(url)
        if self.fetcher is None:
            public_addresses(start)
        result = CrawlResult(start)
        fetcher = self.fetcher or Fetcher(self.config, start)
        policy = CrawlPolicy(fetcher, start, self.config)
        page_in_scope = lambda u: in_crawl_scope(u, start, self.config.crawl_scope, allow_www=True)
        try:
            _, robots_text = policy.rules(start)
            if not policy.can_fetch(start):
                raise ValueError('robots.txt disallows submitted URL')
            log.info('[2/5] Discovering sitemap...')
            sitemap = discover(fetcher, start, robots_text, self.config, policy.can_fetch, result.errors)
            frontier = Frontier(start, self.config.max_discovered, self.config.max_query_variants)
            frontier.add(start, 0, 'start')
            visited, fetched = set(), set()
            seeded = False
            log.info('[3/5] Crawling website...')
            while result.attempted < self.config.max_pages:
                candidate = frontier.pop(result.incoming)
                if candidate is None:
                    break
                current, depth = candidate.url, candidate.depth
                if current in visited or current in fetched:
                    continue
                visited.add(current)
                try:
                    if not policy.page_allowed(current):
                        result.blocked += 1
                        continue
                    result.attempted += 1
                    frontier.visited(current)
                    response = fetcher.get(current, allowed=policy.page_allowed)
                    response.raise_for_status()
                    final = normalize_url(str(response.url))
                    if not policy.page_allowed(final):
                        raise ValueError('Page redirect outside crawl scope or robots permission')
                    if final in fetched:
                        continue
                    fetched.add(final)
                    visited.add(final)
                    if current == start:
                        result.start = final
                    content_type = response.headers.get('content-type', '').lower()
                    if 'text/html' not in content_type and 'application/xhtml+xml' not in content_type:
                        continue
                    page = extract(response.text, final, depth, final)
                    page.links = [link for link in page.links if page_in_scope(link)]
                    page.nav_links = [link for link in page.nav_links if page_in_scope(link)]
                    if not page.content:
                        result.errors.append(f'Page {final}: no extractable text (possibly JavaScript-only)')
                        continue
                    page.last_modified = sitemap.get(current)
                    result.pages.append(page)
                    result.incoming.update(page.links)
                    result.navigation.update(page.nav_links)
                    log.info('Processed %s/%s: %s', result.attempted, self.config.max_pages, final)
                    for link in page.links:
                        if depth < self.config.max_depth and link not in visited and link not in fetched:
                            source = 'navigation' if link in page.nav_links else 'link'
                            frontier.add(link, depth + 1, source, page.link_labels.get(link, ''))
                        if len(result.discovered) < self.config.max_discovered:
                            result.discovered.add(link)
                except RateLimitError as exc:
                    result.errors.append(str(exc))
                    log.warning('%s', exc)
                    break
                except Exception as exc:
                    result.errors.append(f'Page {current}: {exc}')
                    log.warning('%s', result.errors[-1])
                finally:
                    # Admit sitemap candidates after homepage links. Reserve a
                    # quarter of the URL budget for links found on later pages.
                    if not seeded and self.config.max_depth > 0:
                        for seed in sorted(sitemap):
                            seed = site_url(seed, result.start)
                            if page_in_scope(seed) and seed not in visited and seed not in fetched:
                                frontier.add(seed, 1, 'sitemap')
                        seeded = True
            unique = deduplicate(result.pages, result.start)
            result.duplicates_removed = len(result.pages) - len(unique)
            result.pages = unique
            result.discovered.update(frontier.known)
            result.discovered.update(fetched)
            result.requests = fetcher.requests
            result.elapsed = time.monotonic() - started
            return result
        finally:
            if self.fetcher is None:
                fetcher.close()
