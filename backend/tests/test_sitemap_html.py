"""HTML fallback routes must not masquerade as broken XML sitemap files."""
from unittest.mock import patch

import httpx
import pytest

from app.config import CrawlConfig
from app.crawler.crawler import Crawler
from app.crawler.fetcher import Fetcher
from app.crawler.sitemap import discover, parse_sitemap
from test_pipeline import ROOT, FakeFetcher, html

HTML = '<!DOCTYPE html><html><head><title>Generator</title></head><body>Generate llms.txt.</body></html>'


@pytest.mark.parametrize('body', [HTML, HTML.replace('<!DOCTYPE html>', ''),
                                    '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01//EN"><html/>'])
def test_guessed_html_sitemap_is_optional_not_a_partial_crawl(body):
    fetcher = FakeFetcher({'/sitemap.xml': ('text/html', body)})
    errors = []
    assert discover(fetcher, ROOT, '', CrawlConfig(), lambda u: True, errors) == {}
    assert errors == []


def test_robots_advertised_html_sitemap_has_clear_warning():
    fetcher = FakeFetcher({'/sitemap.xml': ('text/html', HTML)})
    errors = []
    assert discover(fetcher, ROOT, 'Sitemap: ' + ROOT + 'sitemap.xml', CrawlConfig(), lambda u: True, errors) == {}
    assert len(errors) == 1
    assert 'returned HTML instead of sitemap XML' in errors[0]
    assert 'entities' not in errors[0]


def test_index_advertised_html_child_has_clear_warning():
    fetcher = FakeFetcher({'/sitemap.xml': ('application/xml',
        f'<sitemapindex><sitemap><loc>{ROOT}child.xml</loc></sitemap></sitemapindex>'),
        '/child.xml': ('text/html', HTML)})
    errors = []
    discover(fetcher, ROOT, '', CrawlConfig(), lambda u: True, errors)
    assert len(errors) == 1 and 'child.xml: Server returned HTML' in errors[0]


def test_xml_is_accepted_even_with_incorrect_html_content_type():
    fetcher = FakeFetcher({'/sitemap.xml': ('text/html',
        f'<urlset><url><loc>{ROOT}about</loc></url></urlset>')})
    errors = []
    assert ROOT + 'about' in discover(fetcher, ROOT, '', CrawlConfig(), lambda u: True, errors)
    assert errors == []


@pytest.mark.parametrize('doctype', [
    '<!DOCTYPE urlset [<!ENTITY x "expanded">]><urlset/>',
    '<!DOCTYPE html [<!ENTITY x SYSTEM "https://private.test/secret">]><html/>',
    '<!DOCTYPE urlset SYSTEM "https://private.test/secret"><urlset/>',
])
def test_xml_entity_and_doctype_guards_remain_enabled(doctype):
    with pytest.raises(ValueError, match='entities'):
        parse_sitemap(doctype.encode())
    fetcher = FakeFetcher({'/sitemap.xml': ('application/xml', doctype)})
    errors = []
    discover(fetcher, ROOT, '', CrawlConfig(), lambda u: True, errors)
    assert len(errors) == 1 and 'entities' in errors[0]


def test_sitemap_redirect_to_app_html_does_not_warn_or_generate_a_job():
    fetcher = Fetcher(CrawlConfig(delay=0, max_pages=1), ROOT)
    requested = []
    def handle(request):
        requested.append(str(request.url.copy_with(host='site.test')))
        if request.url.path == '/robots.txt':
            return httpx.Response(404, content=b'')
        if request.url.path == '/sitemap.xml':
            return httpx.Response(307, headers={'location': '/?url=https%3A%2F%2Fsitemap.xml'})
        return httpx.Response(200, headers={'content-type': 'text/html'}, content=HTML.encode())
    with patch('app.crawler.fetcher.public_addresses', return_value=['8.8.8.8']), \
         patch.object(fetcher.transport, 'handle_request', side_effect=handle):
        # Mock transport returns already buffered responses; emulate a raw stream
        # for sitemap fetching, as a real transport does.
        original = fetcher.transport.handle_request
        def streaming(request):
            response = original(request)
            return httpx.Response(response.status_code, headers=response.headers,
                                  stream=httpx.ByteStream(response.content))
        with patch.object(fetcher.transport, 'handle_request', side_effect=streaming):
            result = Crawler(fetcher.config, fetcher).crawl(ROOT)
    assert result.errors == [] and len(result.pages) == 1
    assert all(url.startswith(ROOT) for url in requested)
    assert all('/api/generate' not in url for url in requested)
    fetcher.close()


def test_missing_gone_sitemap_does_not_warn():
    class Gone(FakeFetcher):
        def get(self, url, allowed=None):
            return httpx.Response(410, request=httpx.Request('GET', url))
    errors = []
    assert discover(Gone(), ROOT, '', CrawlConfig(), lambda u: True, errors) == {}
    assert errors == []
