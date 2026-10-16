"""Conservative exact-content deduplication of fetched pages.

Metadata must match too unless a fetched canonical target confirms the alias.
This avoids merging short pages which merely share template text. No URL is
invented, and every page's outgoing links have already been traversed.
"""
from collections import Counter

from .extractor import clean


def deduplicate(pages, start):
    by_url = {page.url: page for page in pages}
    parents = {url: url for url in by_url}

    def root(url):
        while parents[url] != url:
            parents[url] = parents[parents[url]]
            url = parents[url]
        return url

    def merge(left, right):
        parents[root(left)] = root(right)

    fingerprints = {}
    canonical_votes = Counter()
    for page in pages:
        content = clean(page.content)
        if not content:
            continue
        key = (clean(page.title), clean(page.description), content)
        if key in fingerprints:
            merge(page.url, fingerprints[key])
        else:
            fingerprints[key] = page.url
        target = by_url.get(page.canonical)
        if target and clean(target.content) == content:
            merge(page.url, target.url)
            canonical_votes[target.url] += 1

    groups = {}
    for page in pages:
        groups.setdefault(root(page.url), []).append(page)
    winners = {
        min(group, key=lambda p: (
            p.url != start, -canonical_votes[p.url], p.depth, len(p.url), p.url
        )).url
        for group in groups.values()
    }
    return [page for page in pages if page.url in winners]
