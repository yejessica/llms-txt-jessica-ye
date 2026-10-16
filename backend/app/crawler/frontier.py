"""Bounded candidate sampling and fair, deterministic crawl scheduling."""
from collections import Counter
from dataclasses import dataclass
import hashlib
import heapq
from urllib.parse import urlsplit

IMPORTANT = {"about", "overview", "start", "getting-started", "docs", "documentation",
             "guide", "guides", "tutorial", "reference", "library", "missions", "products",
             "services", "articles", "essays", "pricing", "contact", "browse", "topics",
             "catalog", "catalogue"}


def importance(url, label=""):
    parts = {part.lower().removesuffix('.html').removesuffix('.htm')
             for part in urlsplit(url).path.split('/') if part}
    words = set(label.lower().split())
    return 8 if (parts | words) & IMPORTANT else 0


def bucket(url, start):
    parts = [p for p in urlsplit(url).path.split('/') if p]
    prefix = [p for p in urlsplit(start).path.split('/') if p]
    if prefix and '.' in prefix[-1] and not urlsplit(start).path.endswith('/'):
        prefix.pop()
    if parts[:len(prefix)] == prefix:
        parts = parts[len(prefix):]
    if not parts or (len(parts) == 1 and not urlsplit(url).path.endswith('/')):
        return 'root'
    if len(parts) > 1 and parts[1] in {'category', 'categories', 'author', 'authors', 'tag', 'tags'}:
        return '/'.join(parts[:2])
    return parts[0]


def sample_priority(url):
    # Hash ties avoid taking just the alphabetically first slice of a large map.
    return (importance(url), -len([p for p in urlsplit(url).path.split('/') if p]),
            hashlib.sha256(url.encode()).digest())


class SitemapSample:
    """Retain at most limit URLs, with quotas so a huge section cannot fill it."""
    def __init__(self, limit, start, max_query_variants=5):
        self.limit, self.start = limit, start
        self.quota = max(1, limit // 8)
        self.max_query_variants = max_query_variants
        self.heaps = {}
        self.urls = {}
        self.query_counts = Counter()

    def add(self, url, lastmod=None):
        if url in self.urls:
            self.urls[url] = lastmod
            return
        parsed = urlsplit(url)
        query_key = (parsed.netloc, parsed.path)
        if parsed.query and self.query_counts[query_key] >= self.max_query_variants:
            return
        key = bucket(url, self.start)
        if key not in self.heaps and len(self.heaps) >= 63:
            key = '_other'
        heap = self.heaps.setdefault(key, [])
        candidate = (sample_priority(url), url)
        victim = None
        if len(heap) >= self.quota:
            if candidate <= heap[0]:
                return
            victim = heap
        elif len(self.urls) >= self.limit:
            fullest = max(self.heaps, key=lambda k: (len(self.heaps[k]), k))
            victim = self.heaps[fullest]
            if len(victim) <= len(heap) and candidate <= victim[0]:
                return
        if victim is not None:
            _, removed = heapq.heappop(victim)
            old = urlsplit(removed)
            if old.query:
                self.query_counts[(old.netloc, old.path)] -= 1
            del self.urls[removed]
        heapq.heappush(heap, candidate)
        self.urls[url] = lastmod
        if parsed.query:
            self.query_counts[query_key] += 1


@dataclass
class Candidate:
    url: str
    depth: int
    order: int
    source: str
    label: str = ''


class Frontier:
    """Re-score the bounded frontier as sections are visited and links recur."""
    SOURCE_SCORE = {'start': 1000, 'navigation': 24, 'link': 16, 'sitemap': 0}

    def __init__(self, start, limit, max_query_variants):
        self.start, self.limit = start, limit
        self.max_query_variants = max_query_variants
        self.candidates = {}
        self.known = set()
        self.queries = Counter()
        self.visits = Counter()
        self.counter = 0

    def add(self, url, depth, source='link', label=''):
        previous = self.candidates.get(url)
        if previous:
            previous.depth = min(previous.depth, depth)
            if self.SOURCE_SCORE[source] > self.SOURCE_SCORE[previous.source]:
                previous.source = source
            if label:
                previous.label = label
            return True
        if url in self.known or len(self.known) >= self.limit:
            return False
        if source == 'sitemap' and len(self.known) >= max(1, self.limit * 3 // 4):
            return False
        parsed = urlsplit(url)
        key = (parsed.netloc, parsed.path)
        if parsed.query and self.queries[key] >= self.max_query_variants:
            return False
        self.known.add(url)
        if parsed.query:
            self.queries[key] += 1
        self.candidates[url] = Candidate(url, depth, self.counter, source, label)
        self.counter += 1
        return True

    def pop(self, incoming):
        if not self.candidates:
            return None
        def score(candidate):
            return (self.SOURCE_SCORE[candidate.source] + importance(candidate.url, candidate.label)
                    + (12 if candidate.depth == 1 and candidate.source != 'sitemap' else 0)
                    + 4 / (1 + candidate.depth) + min(incoming[candidate.url], 3)
                    - 6 * self.visits[bucket(candidate.url, self.start)], -candidate.order)
        candidate = max(self.candidates.values(), key=score)
        del self.candidates[candidate.url]
        return candidate

    def visited(self, url):
        if url != self.start:
            self.visits[bucket(url, self.start)] += 1
