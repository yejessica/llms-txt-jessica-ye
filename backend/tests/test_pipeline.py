"""Offline deterministic tests, discoverable by both pytest and unittest."""
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from collections import Counter
import httpx
from app.config import CrawlConfig
from app.crawler.urls import normalize_url, public_addresses, in_scope, relevant, UnsafeURL
from app.crawler.sitemap import parse_sitemap
from app.crawler.extractor import extract, Page
from app.crawler.crawler import Crawler, CrawlResult
from app.crawler.fetcher import Fetcher
from app.analysis.ranking import select_pages
from app.generator.renderer import render
from app.generator.validator import validate
from app.cli import main

ROOT='https://site.test/'
def html(title='Home',links=(),body='Useful information about this website and its purpose.'):
    return f'<html><head><title>{title}</title><meta name="description" content="{body}"></head><body><nav>'+''.join(f'<a href="{u}">{u}</a>' for u in links)+f'</nav><main><h1>{title}</h1><p>{body}</p></main><footer>Unwanted footer</footer></body></html>'

class FakeFetcher:
    def __init__(self,mapping=None):
        self.mapping={'/robots.txt':('text/plain','User-agent: *\nDisallow: /private'),'/':('text/html',html(links=['/about','/docs/','/about#x','/private','/search?q=x','https://else.test/'])),'/about':('text/html',html('About', ['/deep'])),'/docs/':('text/html',html('Docs', ['/deep'])),'/deep':('text/html',html('Deep'))}
        self.mapping.update(mapping or {}); self.calls=[];self.requests=0
    def get(self,url,allowed=None):
        if allowed and not allowed(url): raise UnsafeURL('Blocked')
        self.calls.append(url);self.requests+=1
        value=self.mapping.get(httpx.URL(url).path)
        if isinstance(value,Exception): raise value
        return httpx.Response(200 if value else 404,headers={'content-type':value[0] if value else 'text/plain'},text=value[1] if value else '',request=httpx.Request('GET',url))

class URLTests(unittest.TestCase):
    def test_normalization(self):
        self.assertEqual(normalize_url('HTTPS://EXAMPLE.COM:443/a#x'),'https://example.com/a')
        self.assertEqual(normalize_url('//a///b#x',ROOT),'https://a/b')
    def test_relative_directory_semantics(self):
        self.assertEqual(normalize_url('next','https://example.com/docs/'),'https://example.com/docs/next')
    def test_invalid(self):
        for url in ['ftp://example.com','https://a@b/','https://example.com:9000','https://example.com/\nfoo','https://example.com/\\foo']:
            with self.subTest(url=url),self.assertRaises(ValueError): normalize_url(url)
    def test_scope(self):
        self.assertFalse(in_scope('https://evil.site.test/',ROOT))
        self.assertFalse(in_scope('http://site.test/',ROOT))
    def test_private_dns(self):
        for ip in ['127.0.0.1','10.0.0.1','169.254.169.254','::1','::ffff:8.8.8.8']:
            with self.subTest(ip=ip),patch('socket.getaddrinfo',return_value=[(0,0,0,'',(ip,80))]),self.assertRaises(UnsafeURL): public_addresses(ROOT)
    def test_mixed_dns(self):
        with patch('socket.getaddrinfo',return_value=[(0,0,0,'',('8.8.8.8',80)),(0,0,0,'',('10.0.0.1',80))]),self.assertRaises(UnsafeURL): public_addresses(ROOT)
    def test_public_dns(self):
        with patch('socket.getaddrinfo',return_value=[(0,0,0,'',('8.8.8.8',443))]): self.assertEqual(public_addresses(ROOT),['8.8.8.8'])
    def test_irrelevant(self):
        for url in ['/login','/search?q=a','/a.pdf','/page/2']: self.assertFalse(relevant(normalize_url(url,ROOT)))

class SitemapTests(unittest.TestCase):
    def test_urlset(self):
        kind,entries=parse_sitemap(b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://site.test/a</loc><lastmod>2026-01-01</lastmod></url></urlset>')
        self.assertEqual((kind,entries),('urlset',[('https://site.test/a','2026-01-01')]))
    def test_index(self):
        self.assertEqual(parse_sitemap(b'<sitemapindex><sitemap><loc>https://site.test/a.xml</loc></sitemap></sitemapindex>')[0],'sitemapindex')
    def test_entities(self):
        with self.assertRaises(ValueError): parse_sitemap(b'<!DOCTYPE a [<!ENTITY a "x">]><urlset/>')
    def test_invalid(self):
        with self.assertRaises(Exception): parse_sitemap(b'<broken')

class CrawlTests(unittest.TestCase):
    def crawl(self,**kwargs):
        f=FakeFetcher();r=Crawler(CrawlConfig(delay=0,**kwargs),f).crawl(ROOT);return f,r
    def test_bfs_and_dedup(self):
        f,r=self.crawl()
        self.assertEqual([p.url for p in r.pages],[ROOT,ROOT+'about',ROOT+'docs/',ROOT+'deep'])
        self.assertEqual(len(f.calls),len(set(f.calls)))
        self.assertEqual([p.depth for p in r.pages],[0,1,1,2])
        self.assertEqual(r.incoming[ROOT+'deep'],2)
    def test_depth_limit(self):
        _,r=self.crawl(max_depth=0);self.assertEqual(len(r.pages),1)
    def test_page_limit(self):
        _,r=self.crawl(max_pages=2);self.assertEqual(r.attempted,2);self.assertEqual(len(r.pages),2)
    def test_robots(self):
        f,r=self.crawl();self.assertNotIn(ROOT+'private',f.calls);self.assertEqual(r.blocked,1)
    def test_failures_continue(self):
        f=FakeFetcher({'/about':TimeoutError('timeout')});r=Crawler(CrawlConfig(delay=0),f).crawl(ROOT)
        self.assertEqual(len(r.errors),1);self.assertEqual(len(r.pages),3)
    def test_robots_fail_closed(self):
        with self.assertRaises(ValueError): Crawler(fetcher=FakeFetcher({'/robots.txt':TimeoutError()})).crawl(ROOT)
    def test_sitemap_supplement_and_scope(self):
        f=FakeFetcher({'/sitemap.xml':('application/xml','<sitemapindex><sitemap><loc>https://site.test/child.xml</loc></sitemap></sitemapindex>'),'/child.xml':('application/xml','<urlset><url><loc>https://site.test/orphan</loc><lastmod>2026-01-01</lastmod></url><url><loc>https://evil.test/</loc></url></urlset>'),'/orphan':('text/html',html('Orphan'))})
        r=Crawler(CrawlConfig(delay=0),f).crawl(ROOT)
        self.assertEqual(r.pages[-1].url,ROOT+'orphan');self.assertEqual(r.pages[-1].last_modified,'2026-01-01')
        self.assertNotIn('https://evil.test/',r.discovered)
    def test_empty_js_shell(self):
        f=FakeFetcher({"/":("text/html","<html><script>render()</script></html>")})
        result=Crawler(CrawlConfig(delay=0),f).crawl(ROOT)
        self.assertEqual(result.pages,[])
        self.assertIn("possibly JavaScript-only",result.errors[0])
    def test_verified_canonical(self):
        body=html("Same")
        f=FakeFetcher({"/":("text/html",html(links=["/a","/b"])),"/a":("text/html",body+'<link rel="canonical" href="/b">'),"/b":("text/html",body)})
        result=Crawler(CrawlConfig(delay=0),f).crawl(ROOT)
        self.assertNotIn(ROOT+"a",[p.url for p in result.pages])
        self.assertIn(ROOT+"b",[p.url for p in result.pages])
    def test_limits_validation(self):
        for kwargs in [{'max_pages':0},{'max_depth':-1}]:
            with self.assertRaises(ValueError): CrawlConfig(**kwargs)

class ExtractionTests(unittest.TestCase):
    def test_metadata_and_boilerplate(self):
        page=extract(html(links=['/about','/about#fragment']),ROOT,0,ROOT)
        self.assertEqual(page.title,'Home');self.assertEqual(page.headings,['Home'])
        self.assertEqual(page.links,[ROOT+'about']);self.assertEqual(page.nav_links,page.links)
        self.assertNotIn('Unwanted footer',page.content);self.assertIn('Useful information',page.description)
    def test_missing_metadata(self):
        page=extract('<h1>Title</h1><p>A real paragraph containing useful information about the project.</p>',ROOT,0,ROOT)
        self.assertEqual(page.title,'Title');self.assertTrue(page.description.startswith('A real paragraph'))
    def test_canonical_scope(self):
        page=extract('<link rel="canonical" href="http://127.0.0.1/"><h1>Title</h1>',ROOT,0,ROOT)
        self.assertEqual(page.url,ROOT);self.assertIsNone(page.canonical)
    def test_empty_content(self):
        page=extract('<script>renderApp()</script>',ROOT,0,ROOT)
        self.assertEqual(page.description,'');self.assertEqual(page.content,'')

class RankingRendererTests(unittest.TestCase):
    def setUp(self):
        self.result=Crawler(CrawlConfig(delay=0),FakeFetcher()).crawl(ROOT)
    def test_deterministic_and_home(self):
        a=select_pages(self.result,3);self.assertEqual(a,select_pages(self.result,3));self.assertEqual(a[0].url,ROOT)
    def test_diversity(self):
        self.result.pages=[Page(ROOT+'docs/'+str(i),'Doc','Desc',[],'content',1) for i in range(10)]+[Page(ROOT+'blog/post','Post','Desc',[],'content',1)]
        self.assertIn(ROOT+'blog/post',[p.url for p in select_pages(self.result,2)])
    def test_render_and_validate(self):
        selected=select_pages(self.result);md=render(ROOT,self.result.pages,selected)
        self.assertEqual(validate(md,ROOT,self.result.pages,selected),[])
        self.assertIn('# Home',md);self.assertIn('## Documentation',md)
    def test_duplicates_rejected(self):
        selected=[self.result.pages[0]]*2
        self.assertIn('Duplicate URL',validate(render(ROOT,self.result.pages,selected),ROOT,self.result.pages,selected))
    def test_tamper_rejected(self):
        selected=select_pages(self.result)
        self.assertTrue(validate(render(ROOT,self.result.pages,selected)+'Invented',ROOT,self.result.pages,selected))
    def test_escaping(self):
        p=Page(ROOT+'a(b)','Title [x]','Description *x*',[],'',0)
        md=render(p.url,[p],[p]);self.assertIn('a%28b%29',md);self.assertIn('Title \\[x\\]',md)
    def test_minimal(self):
        p=Page(ROOT,'Site','',[],'',0)
        self.assertEqual(validate(render(ROOT,[p],[]),ROOT,[p],[]),[])

class FetcherTests(unittest.TestCase):
    def test_pinned_ip_and_sni(self):
        f=Fetcher(CrawlConfig(delay=0),ROOT)
        def handle(request):
            self.assertEqual(request.url.host,'8.8.8.8');self.assertEqual(request.headers['host'],'site.test')
            self.assertEqual(request.extensions['sni_hostname'],'site.test')
            return httpx.Response(200,content=b'ok')
        with patch('app.crawler.fetcher.public_addresses',return_value=['8.8.8.8']),patch.object(f.transport,'handle_request',side_effect=handle):
            self.assertEqual(f.get(ROOT).text,'ok')
        f.close()
    def test_redirect_scope(self):
        f=Fetcher(CrawlConfig(delay=0),ROOT)
        with patch('app.crawler.fetcher.public_addresses',return_value=['8.8.8.8']),patch.object(f.transport,'handle_request',return_value=httpx.Response(302,headers={'location':'http://127.0.0.1/'})),self.assertRaises(UnsafeURL): f.get(ROOT)
        self.assertEqual(f.requests,1);f.close()
    def test_cached_redirect_avoids_duplicate_request(self):
        f=Fetcher(CrawlConfig(delay=0),ROOT)
        responses=[httpx.Response(302,headers={"location":"/about"}),httpx.Response(200,content=b"ok")]
        with patch("app.crawler.fetcher.public_addresses",return_value=["8.8.8.8"]),patch.object(f.transport,"handle_request",side_effect=responses):
            f.get(ROOT);f.get(ROOT+"about")
        self.assertEqual(f.requests,2);f.close()
    def test_robots_redirect_check(self):
        f=Fetcher(CrawlConfig(delay=0),ROOT)
        with patch("app.crawler.fetcher.public_addresses",return_value=["8.8.8.8"]),patch.object(f.transport,"handle_request",return_value=httpx.Response(302,headers={"location":"/private"})),self.assertRaises(UnsafeURL):
            f.get(ROOT,allowed=lambda u: not u.endswith("private"))
        self.assertEqual(f.requests,1);f.close()
    def test_size_limit(self):
        f=Fetcher(CrawlConfig(delay=0,max_bytes=2),ROOT)
        with patch('app.crawler.fetcher.public_addresses',return_value=['8.8.8.8']),patch.object(f.transport,'handle_request',return_value=httpx.Response(200,content=b'large')),self.assertRaises(ValueError): f.get(ROOT)
        f.close()

class CLITests(unittest.TestCase):
    def test_success_output_and_stats(self):
        result=Crawler(CrawlConfig(delay=0),FakeFetcher()).crawl(ROOT)
        with tempfile.TemporaryDirectory() as directory, patch("app.cli.Crawler") as crawler:
            crawler.return_value.crawl.return_value=result
            output=Path(directory)/"llms.txt"
            self.assertEqual(main(["generate",ROOT,"--output",str(output)]),0)
            self.assertTrue(output.read_text().startswith("# Home"))
            self.assertTrue(output.with_suffix(".stats.json").exists())
    def test_invalid_limit_exit(self):
        self.assertEqual(main(["generate",ROOT,"--max-pages","0"]),1)

if __name__=='__main__': unittest.main()
