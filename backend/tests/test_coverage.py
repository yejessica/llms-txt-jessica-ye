"""Regression coverage for compressed discovery, directory scope and duplicates."""
import gzip
from unittest.mock import patch

import httpx
import pytest

from app.config import CrawlConfig
from app.crawler.crawler import Crawler
from app.crawler.dedup import deduplicate
from app.crawler.extractor import Page
from app.crawler.sitemap import parse_sitemap
from app.crawler.urls import in_crawl_scope, relevant
from app.settings import Settings
from app.cli import main
from test_pipeline import ROOT, FakeFetcher, html


def test_gzip_sitemap_and_index():
    xml = b'<urlset><url><loc>https://site.test/docs/orphan</loc><lastmod>2026-10-16</lastmod></url></urlset>'
    assert parse_sitemap(gzip.compress(xml)) == parse_sitemap(xml)
    index = b'<sitemapindex><sitemap><loc>https://site.test/maps/child.xml.gz</loc></sitemap></sitemapindex>'

    class BinaryFetcher(FakeFetcher):
        def get(self, url, allowed=None):
            blobs = {ROOT + 'sitemap.xml': gzip.compress(index),
                     ROOT + 'maps/child.xml.gz': gzip.compress(xml)}
            if url in blobs:
                self.calls.append(url)
                self.requests += 1
                return httpx.Response(200, content=blobs[url], request=httpx.Request('GET', url))
            return super().get(url, allowed)

    fetcher = BinaryFetcher({'/docs/': ('text/html', html('Docs')),
                             '/docs/orphan': ('text/html', html('Orphan'))})
    result = Crawler(CrawlConfig(delay=0), fetcher).crawl(ROOT + 'docs/')
    assert [p.url for p in result.pages] == [ROOT + 'docs/', ROOT + 'docs/orphan']
    assert result.pages[-1].last_modified == '2026-10-16'


@pytest.mark.parametrize('data', [b'<urlset>' + b' ' * 5000 + b'</urlset>',
                                  gzip.compress(b'<urlset>' + b' ' * 5000 + b'</urlset>')])
def test_sitemap_size_limit_before_and_after_decompression(data):
    with pytest.raises(ValueError, match='size limit'):
        parse_sitemap(data, max_bytes=200)


def test_corrupt_compression_and_compressed_entities_rejected():
    with pytest.raises((EOFError, OSError)):
        parse_sitemap(gzip.compress(b'<urlset/>')[:-5])
    with pytest.raises(ValueError, match='entities'):
        parse_sitemap(gzip.compress(b'<!DOCTYPE x [<!ENTITY a "x">]><urlset/>'))
    with pytest.raises(ValueError):
        parse_sitemap(gzip.compress('<!DOCTYPE x [<!ENTITY a "x">]><urlset/>'.encode('utf-16')))


@pytest.mark.parametrize('url,expected', [
    ('/3/', True), ('/3/tutorial/index.html', True), ('/30/', False),
    ('/3.14/', False), ('/fr/3/', False), ('/3/../2/', False),
    ('/3/%2e%2e/2/', False), ('/3%2f..%2f2/', False),
    ('https://other.test/3/a', False), ('/3', True),
])
def test_directory_boundaries(url, expected):
    from app.crawler.urls import normalize_url
    assert in_crawl_scope(normalize_url(url, ROOT), ROOT + '3/') is expected


def test_scope_modes_and_filename():
    assert not in_crawl_scope(ROOT + 'docs/v1.3/a', ROOT + 'docs/v1.2/')
    assert in_crawl_scope(ROOT + 'docs/next', ROOT + 'docs/index.html')
    assert not in_crawl_scope(ROOT + 'elsewhere', ROOT + 'docs/index.html')
    assert in_crawl_scope(ROOT + 'docs/a', ROOT + 'docs')
    assert in_crawl_scope(ROOT + 'elsewhere', ROOT + 'docs/', 'site')
    assert not in_crawl_scope('https://other.test/elsewhere', ROOT, 'site')


def test_documentation_archive_downloads_do_not_consume_page_budget():
    for extension in ('tar.bz2', 'tar.xz', 'tgz', 'epub'):
        assert not relevant(ROOT + 'archives/docs.' + extension)
    assert relevant(ROOT + 'download.html')


def test_scoped_links_sitemap_and_origin_metadata():
    sitemap = '<urlset>' + ''.join(f'<url><loc>{ROOT}{path}</loc></url>' for path in
                                  ['docs/orphan', 'elsewhere', 'docs-other']) + '</urlset>'
    fetcher = FakeFetcher({
        '/docs/': ('text/html', html('Docs', ['/docs/a', '/outside', '/docs-other'])),
        '/docs/a': ('text/html', html('A')),
        '/docs/orphan': ('text/html', html('Orphan')),
        '/sitemap.xml': ('application/xml', sitemap),
    })
    result = Crawler(CrawlConfig(delay=0), fetcher).crawl(ROOT + 'docs/')
    assert {page.url for page in result.pages} == {ROOT + 'docs/', ROOT + 'docs/a', ROOT + 'docs/orphan'}
    assert ROOT + 'robots.txt' in fetcher.calls and ROOT + 'sitemap.xml' in fetcher.calls
    assert ROOT + 'outside' not in fetcher.calls
    assert all('/docs/' in url for url in result.discovered)
    site_result = Crawler(CrawlConfig(delay=0, crawl_scope='site'), fetcher).crawl(ROOT + 'docs/')
    assert ROOT + 'outside' in fetcher.calls
    assert ROOT + 'elsewhere' in site_result.discovered


def test_out_of_directory_redirect_cannot_be_fetched():
    class RedirectFetcher(FakeFetcher):
        def get(self, url, allowed=None):
            if url == ROOT + 'docs/escape':
                assert not allowed(ROOT + 'outside')
                raise ValueError('Redirect outside crawl scope')
            return super().get(url, allowed)
    fetcher = RedirectFetcher({'/docs/': ('text/html', html('Docs', ['/docs/escape']))})
    result = Crawler(CrawlConfig(delay=0), fetcher).crawl(ROOT + 'docs/')
    assert len(result.pages) == 1
    assert any('outside crawl scope' in error for error in result.errors)


def test_scope_environment_and_cli(tmp_path, monkeypatch):
    monkeypatch.setenv('LLMS_CRAWL_SCOPE', 'site')
    assert Settings.from_env().crawl_config().crawl_scope == 'site'
    with pytest.raises(ValueError):
        CrawlConfig(crawl_scope='invalid')
    with patch('app.cli.Crawler') as crawler:
        crawler.return_value.crawl.return_value = Crawler(CrawlConfig(delay=0), FakeFetcher()).crawl(ROOT)
        assert main(['generate', ROOT, '--crawl-scope', 'site', '--output', str(tmp_path / 'llms.txt')]) == 0
        assert crawler.call_args.args[0].crawl_scope == 'site'


def page(path, title='Same', content='Identical extracted body', canonical=None):
    return Page(ROOT + path, title, 'Description', [], content, 1, canonical=canonical)


def test_duplicate_content_keeps_home_and_follows_both_link_sets():
    fetcher = FakeFetcher({
        '/': ('text/html', html('Home', ['/index.html', '/about'])),
        '/index.html': ('text/html', html('Home', ['/docs/'])),
    })
    result = Crawler(CrawlConfig(delay=0), fetcher).crawl(ROOT)
    assert ROOT in [p.url for p in result.pages]
    assert ROOT + 'index.html' not in [p.url for p in result.pages]
    assert ROOT + 'docs/' in fetcher.calls
    assert result.duplicates_removed == 1


def test_same_boilerplate_different_metadata_and_distinct_body_survive():
    pages = [page('a', title='Contact'), page('b', title='About'),
             page('c', title='Contact', content='Different substantive body')]
    assert deduplicate(pages, ROOT) == pages


def test_duplicate_choice_stable_and_canonical_cycles_keep_one():
    pages = [page('index.html'), page('')]
    assert deduplicate(pages, ROOT) == [pages[1]]
    assert deduplicate(list(reversed(pages)), ROOT) == [pages[1]]
    pages = [page('a', canonical=ROOT + 'b'), page('b', canonical=ROOT + 'a')]
    assert len(deduplicate(pages, ROOT)) == 1


def test_verified_canonical_preferred_and_unfetched_canonical_ignored():
    pages = [page('alias', title='Alias', canonical=ROOT + 'target'),
             page('target', title='Target')]
    assert deduplicate(pages, ROOT) == [pages[1]]
    pages = [page('alias', canonical=ROOT + 'missing')]
    assert deduplicate(pages, ROOT) == pages
    pages.append(page('target', content='Actually different'))
    pages[0].canonical = pages[1].url
    assert deduplicate(pages, ROOT) == pages
