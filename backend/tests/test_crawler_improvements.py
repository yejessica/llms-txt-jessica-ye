"""Behavioral coverage for crawl diversity, bounded maps and URL compatibility."""
from contextlib import contextmanager
import gzip
from unittest.mock import patch
from urllib.parse import urlsplit

import httpx
import pytest

from app.config import CrawlConfig
from app.crawler.crawler import Crawler
from app.crawler.extractor import extract
from app.crawler.fetcher import Fetcher
from app.crawler.frontier import SitemapSample
from app.crawler.policy import CrawlPolicy
from app.crawler.sitemap import parse_sitemap_stream, discover
from app.crawler.urls import normalize_url, relevant, site_url, site_origin_allowed, UnsafeURL
from app.settings import Settings
from test_pipeline import ROOT, FakeFetcher, html


class Stream(httpx.SyncByteStream):
    def __init__(self, chunks):
        self.chunks, self.closed = chunks, False

    def __iter__(self):
        yield from self.chunks

    def close(self):
        self.closed = True


def test_small_budget_samples_products_before_exhausting_categories():
    categories = [f'/catalogue/category/{i}/' for i in range(40)]
    product = '/catalogue/a-product/index.html'
    mapping = {'/': ('text/html', html('Home', categories + [product]))}
    mapping.update({p: ('text/html', html(p)) for p in categories + [product]})
    result = Crawler(CrawlConfig(delay=0, max_pages=4), FakeFetcher(mapping)).crawl(ROOT)
    assert ROOT + product.lstrip('/') in {p.url for p in result.pages}
    assert any('/category/' in p.url for p in result.pages)
    assert result.attempted == 4


def test_overview_and_other_sections_survive_many_news_links():
    paths = [f'/news/story-{i}' for i in range(50)] + ['/missions/', '/about/']
    mapping = {'/': ('text/html', html('Home', paths))}
    mapping.update({p: ('text/html', html(p)) for p in paths})
    result = Crawler(CrawlConfig(delay=0, max_pages=4), FakeFetcher(mapping)).crawl(ROOT)
    assert {ROOT + 'missions/', ROOT + 'about/'} <= {p.url for p in result.pages}


def test_deeper_navigation_does_not_displace_homepage_section_overview():
    nested = [f'/docs/reference/page-{i}' for i in range(30)]
    fetcher = FakeFetcher({'/': ('text/html', html('Home', ['/docs/', '/library/'])),
                           '/docs/': ('text/html', html('Docs', nested)),
                           '/library/': ('text/html', html('Library'))})
    result = Crawler(CrawlConfig(delay=0, max_pages=3), fetcher).crawl(ROOT)
    assert {ROOT, ROOT + 'docs/', ROOT + 'library/'} == {p.url for p in result.pages}


def test_flat_homepage_links_do_not_starve_a_section_discovered_later():
    paths = [f'/service-{i}' for i in range(40)] + ['/browse']
    mapping = {'/': ('text/html', html('Home', paths)),
               '/browse': ('text/html', html('Browse', ['/browse/benefits']))}
    mapping.update({p: ('text/html', html(p)) for p in paths if p != '/browse'})
    mapping['/browse/benefits'] = ('text/html', html('Benefits'))
    result = Crawler(CrawlConfig(delay=0, max_pages=6), FakeFetcher(mapping)).crawl(ROOT)
    assert ROOT + 'browse/benefits' in {p.url for p in result.pages}


def test_total_sitemap_budget_reports_partial_discovery_without_publishing_invalid_map():
    fetcher = FakeFetcher({'/sitemap.xml': ('application/xml', '<urlset>' + ' ' * 2000 + '</urlset>')})
    errors = []
    assert discover(fetcher, ROOT, '', CrawlConfig(max_sitemap_total_bytes=1000), lambda u: True, errors) == {}
    assert 'total byte budget' in errors[0]


def test_trickling_sitemap_checks_deadline_before_buffering_whole_chunk():
    class Slow(FakeFetcher):
        @contextmanager
        def stream(self, url, allowed=None):
            response = httpx.Response(200, stream=Stream([b'<' for _ in range(100)]),
                                      request=httpx.Request('GET', url))
            try:
                yield response
            finally:
                response.close()
    errors = []
    # Discovery starts at 0, checks before the request at 0, then observes a
    # deadline on the first tiny raw chunk; no XML parse error should mask it.
    with patch('app.crawler.sitemap.time.monotonic', side_effect=[0, 0, 31]):
        assert discover(Slow(), ROOT, '', CrawlConfig(), lambda u: True, errors) == {}
    assert 'time budget' in errors[0]


def test_image_map_navigation_and_image_alt_text_are_discovered():
    page = extract('<title>Legacy</title><map><area href="articles.html" alt="Essays"></map>'
                   '<a href="about.html"><img alt="About"></a><p>Public website.</p>', ROOT, 0, ROOT)
    assert ROOT + 'articles.html' in page.links and ROOT + 'articles.html' in page.nav_links
    assert page.link_labels[ROOT + 'articles.html'] == 'Essays'
    assert page.link_labels[ROOT + 'about.html'] == 'About'


def test_later_links_are_not_crowded_out_by_sitemap_seeds():
    sitemap = '<urlset>' + ''.join(f'<url><loc>{ROOT}news/{i}</loc></url>' for i in range(100)) + '</urlset>'
    fetcher = FakeFetcher({'/sitemap.xml': ('application/xml', sitemap),
                           '/about': ('text/html', html('About', ['/new-guide'])),
                           '/new-guide': ('text/html', html('Guide'))})
    result = Crawler(CrawlConfig(delay=0, max_discovered=20), fetcher).crawl(ROOT)
    assert ROOT + 'new-guide' in {p.url for p in result.pages}


def map_chunks(count):
    yield b'<?xml version="1.0" encoding="UTF-8"?><urlset>'
    for i in range(count):
        yield f'<url><loc>{ROOT}news/{i}-long-article-slug-for-streaming-coverage</loc></url>'.encode()
    yield f'<url><loc>{ROOT}missions/</loc></url></urlset>'.encode()


def test_large_map_streams_beyond_page_limit_and_keeps_late_overview():
    config = CrawlConfig(delay=0, max_discovered=80)
    fetcher = Fetcher(config, ROOT)
    streams = []
    def handle(request):
        stream = Stream(map_chunks(30000))
        streams.append(stream)
        return httpx.Response(200, stream=stream)
    with patch('app.crawler.fetcher.public_addresses', return_value=['8.8.8.8']), \
         patch.object(fetcher.transport, 'handle_request', side_effect=handle):
        errors = []
        urls = discover(fetcher, ROOT, '', config, lambda u: True, errors)
    assert not errors
    assert ROOT + 'missions/' in urls
    assert 1 < len(urls) <= config.max_discovered
    assert streams[0].closed and ROOT + 'sitemap.xml' not in fetcher.cache
    fetcher.close()


def test_normal_pages_keep_small_response_limit():
    fetcher = Fetcher(CrawlConfig(delay=0, max_bytes=100, max_sitemap_bytes=10000), ROOT)
    stream = Stream([b'x' * 101])
    with patch('app.crawler.fetcher.public_addresses', return_value=['8.8.8.8']), \
         patch.object(fetcher.transport, 'handle_request', return_value=httpx.Response(200, stream=stream)):
        with pytest.raises(ValueError, match='Response exceeds'):
            fetcher.get(ROOT)
    assert stream.closed
    fetcher.close()


def test_streaming_parser_entry_byte_and_field_limits():
    with pytest.raises(ValueError, match='entry limit'):
        parse_sitemap_stream(map_chunks(3), lambda *args: None, max_entries=2)
    with pytest.raises(ValueError, match='size limit'):
        parse_sitemap_stream([gzip.compress(b'<urlset>' + b' ' * 100000 + b'</urlset>')],
                             lambda *args: None, max_bytes=1000)
    with pytest.raises(ValueError, match='Compressed sitemap'):
        parse_sitemap_stream([gzip.compress(b'<urlset/>')], lambda *args: None, max_compressed_bytes=5)
    with pytest.raises(ValueError, match='field length'):
        parse_sitemap_stream([b'<urlset><url><loc>' + b'a' * 9000 + b'</loc></url></urlset>'], lambda *args: None)


@pytest.mark.parametrize('xml', [b'<!DOCTYPE x [<!ENTITY x "expanded">]><urlset/>',
                                b'<!DOCTYPE x SYSTEM "https://private.test/secret"><urlset/>'])
def test_chunk_boundary_entity_guards(xml):
    with pytest.raises(ValueError, match='entities'):
        parse_sitemap_stream((xml[i:i+1] for i in range(len(xml))), lambda *args: None)


def test_late_malformed_map_does_not_publish_earlier_candidates():
    class Broken(FakeFetcher):
        @contextmanager
        def stream(self, url, allowed=None):
            response = httpx.Response(200, stream=Stream([
                f'<urlset><url><loc>{ROOT}poison</loc></url>'.encode()]), request=httpx.Request('GET', url))
            try:
                yield response
            finally:
                response.close()
    errors = []
    assert discover(Broken(), ROOT, '', CrawlConfig(delay=0), lambda u: True, errors) == {}
    assert errors


def test_sitemap_sample_diversity_and_query_variation_bound():
    sample = SitemapSample(80, ROOT, 2)
    for i in range(1000):
        sample.add(ROOT + f'news/story-{i}')
    for i in range(100):
        sample.add(ROOT + f'products/item?id={i}')
    sample.add(ROOT + 'about/')
    assert ROOT + 'about/' in sample.urls
    assert sum('/products/' in url for url in sample.urls) <= 2
    assert len(sample.urls) <= 80


def test_query_normalization_preserves_content_and_order():
    assert normalize_url(ROOT + '?page_id=123&utm_source=email&lang=fr&fbclid=x') == ROOT + '?page_id=123&lang=fr'
    assert normalize_url(ROOT + '?id=2&id=1&name=a%20b&UTM_campaign=x') == ROOT + '?id=2&id=1&name=a%20b'
    assert relevant(ROOT + '?page_id=123') and relevant(ROOT + '?lang=fr')
    for query in ['?sort=price', '?sessionid=secret', '/search?q=term']:
        assert not relevant(ROOT + query)


def test_meaningful_query_reaches_transport_without_tracking_data():
    fetcher = Fetcher(CrawlConfig(delay=0), ROOT)
    def handle(request):
        assert request.url.query == b'page_id=123&lang=fr'
        return httpx.Response(200, content=b'Content page')
    with patch('app.crawler.fetcher.public_addresses', return_value=['8.8.8.8']), \
         patch.object(fetcher.transport, 'handle_request', side_effect=handle):
        result = fetcher.get(ROOT + '?page_id=123&utm_source=email&lang=fr')
    assert str(result.url) == ROOT + '?page_id=123&lang=fr'
    fetcher.close()


def test_query_traps_cannot_starve_other_pages():
    paths = [f'/?page_id={i}' for i in range(100)] + ['/about']
    fetcher = FakeFetcher({'/': ('text/html', html('Home', paths))})
    result = Crawler(CrawlConfig(delay=0, max_query_variants=2, max_pages=10), fetcher).crawl(ROOT)
    assert len([url for url in fetcher.calls if urlsplit(url).query]) <= 2
    assert ROOT + 'about' in {p.url for p in result.pages}


def test_origin_policy_only_accepts_exact_www_pair_and_standard_ports():
    assert site_origin_allowed('https://www.site.test/docs', ROOT)
    for url in ['https://docs.site.test/', 'https://site.test.evil/', 'http://site.test/',
                'https://www.site.test:80/', 'https://www.www.site.test/']:
        assert not site_origin_allowed(url, ROOT)
    assert site_url('http://www.site.test:80/docs/', ROOT) == 'https://www.site.test/docs/'
    assert site_url('http://else.test/', ROOT) == 'http://else.test/'


def test_redirect_alias_checks_destination_robots_before_fetching_page():
    fetcher = Fetcher(CrawlConfig(delay=0), ROOT)
    seen = []
    def handle(request):
        host, path = request.headers['host'], request.url.path
        seen.append((host, path))
        if path == '/robots.txt':
            return httpx.Response(200, content=b'User-agent: *\nDisallow: /' if host == 'www.site.test' else b'')
        return httpx.Response(302, headers={'location': 'https://www.site.test/'})
    policy = CrawlPolicy(fetcher, ROOT, fetcher.config)
    with patch('app.crawler.fetcher.public_addresses', return_value=['8.8.8.8']), \
         patch.object(fetcher.transport, 'handle_request', side_effect=handle):
        with pytest.raises(UnsafeURL):
            fetcher.get(ROOT, allowed=policy.page_allowed)
    assert ('www.site.test', '/robots.txt') in seen and ('www.site.test', '/') not in seen
    fetcher.close()


def test_alias_redirect_pins_each_connection_and_keeps_https():
    fetcher = Fetcher(CrawlConfig(delay=0), ROOT)
    seen = []
    def handle(request):
        assert request.url.host == '8.8.8.8' and request.url.scheme == 'https'
        host, path = request.headers['host'], request.url.path
        assert request.extensions['sni_hostname'] == host
        seen.append((host, path))
        if path == '/robots.txt':
            return httpx.Response(404, content=b'')
        if host == 'site.test':
            return httpx.Response(301, headers={'location': 'http://www.site.test/guide/'})
        return httpx.Response(200, content=b'Guide')
    policy = CrawlPolicy(fetcher, ROOT, fetcher.config)
    with patch('app.crawler.fetcher.public_addresses', return_value=['8.8.8.8']) as dns, \
         patch.object(fetcher.transport, 'handle_request', side_effect=handle):
        response = fetcher.get(ROOT, allowed=policy.page_allowed)
    assert str(response.url) == 'https://www.site.test/guide/' and dns.call_count == len(seen)
    fetcher.close()


def test_alias_private_dns_is_rejected_before_connection():
    fetcher = Fetcher(CrawlConfig(delay=0), ROOT)
    policy = CrawlPolicy(fetcher, ROOT, fetcher.config)
    def addresses(url):
        if 'www.' in url:
            raise UnsafeURL('Destination resolves to a non-public IP')
        return ['8.8.8.8']
    def handle(request):
        if request.url.path == '/robots.txt':
            return httpx.Response(404, content=b'')
        return httpx.Response(302, headers={'location': 'https://www.site.test/'})
    with patch('app.crawler.fetcher.public_addresses', side_effect=addresses), \
         patch.object(fetcher.transport, 'handle_request', side_effect=handle) as transport:
        with pytest.raises(ValueError, match='robots.txt permission'):
            fetcher.get(ROOT, allowed=policy.page_allowed)
    assert transport.call_count == 2
    fetcher.close()


def test_gzip_http_page_is_not_decoded_twice():
    fetcher = Fetcher(CrawlConfig(delay=0), ROOT)
    with patch('app.crawler.fetcher.public_addresses', return_value=['8.8.8.8']), \
         patch.object(fetcher.transport, 'handle_request', return_value=httpx.Response(
             200, headers={'content-encoding': 'gzip'}, stream=Stream([gzip.compress(b'<h1>Page</h1>')]))):
        assert fetcher.get(ROOT).text == '<h1>Page</h1>'
    fetcher.close()


def test_non_ascii_response_headers_are_preserved_as_raw_bytes():
    fetcher = Fetcher(CrawlConfig(delay=0), ROOT)
    response = httpx.Response(200, headers=[(b'x-message', '\u26c4'.encode())], content=b'Page')
    with patch('app.crawler.fetcher.public_addresses', return_value=['8.8.8.8']), \
         patch.object(fetcher.transport, 'handle_request', return_value=response):
        assert fetcher.get(ROOT).text == 'Page'
    fetcher.close()


def test_settings_propagate_separate_sitemap_budgets(monkeypatch):
    monkeypatch.setenv('LLMS_MAX_SITEMAP_BYTES', '4000000')
    monkeypatch.setenv('LLMS_MAX_QUERY_VARIANTS', '3')
    config = Settings.from_env().crawl_config()
    assert config.max_sitemap_bytes == 4000000 and config.max_query_variants == 3
    assert config.max_bytes == 2000000
