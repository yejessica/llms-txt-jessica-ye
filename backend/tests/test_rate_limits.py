from datetime import datetime, timezone
import unittest
from unittest.mock import patch

import httpx

from app.config import CrawlConfig
from app.crawler.crawler import Crawler
from app.crawler.fetcher import Fetcher, RateLimitError, retry_after_seconds
from app.crawler.urls import relevant
from test_pipeline import ROOT, FakeFetcher


class RateLimitTests(unittest.TestCase):
    def test_retry_after_seconds_and_date(self):
        self.assertEqual(retry_after_seconds("12"), 12)
        now = datetime(2026, 10, 15, tzinfo=timezone.utc)
        self.assertEqual(retry_after_seconds("Thu, 15 Oct 2026 00:00:20 GMT", now), 20)
        self.assertEqual(retry_after_seconds("Wed, 14 Oct 2026 00:00:00 GMT", now), 0)
        self.assertEqual(retry_after_seconds("invalid"), 5)
        self.assertEqual(retry_after_seconds(None), 5)

    def test_retry_once_and_do_not_cache_429(self):
        fetcher = Fetcher(CrawlConfig(delay=0), ROOT)
        responses = [httpx.Response(429, headers={"Retry-After": "3"}), httpx.Response(200, content=b"ok")]
        try:
            with patch("app.crawler.fetcher.public_addresses", return_value=["8.8.8.8"]), patch.object(fetcher.transport, "handle_request", side_effect=responses), patch("app.crawler.fetcher.time.sleep") as sleep:
                response = fetcher.get(ROOT)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(fetcher.requests, 2)
            self.assertEqual(fetcher.cache[ROOT].status_code, 200)
            self.assertEqual(fetcher.minimum_delay, 2)
            self.assertIn(unittest.mock.call(3), sleep.call_args_list)
        finally:
            fetcher.close()

    def test_long_retry_after_stops_without_sleep_or_retry(self):
        fetcher = Fetcher(CrawlConfig(delay=0), ROOT)
        try:
            with patch("app.crawler.fetcher.public_addresses", return_value=["8.8.8.8"]), patch.object(fetcher.transport, "handle_request", return_value=httpx.Response(429, headers={"Retry-After": "3600"})), patch("app.crawler.fetcher.time.sleep") as sleep, self.assertRaises(RateLimitError):
                fetcher.get(ROOT)
            sleep.assert_not_called()
            self.assertEqual(fetcher.requests, 1)
            self.assertNotIn(ROOT, fetcher.cache)
        finally:
            fetcher.close()

    def test_repeated_429_stops_after_one_retry(self):
        fetcher = Fetcher(CrawlConfig(delay=0), ROOT)
        try:
            with patch("app.crawler.fetcher.public_addresses", return_value=["8.8.8.8"]), patch.object(fetcher.transport, "handle_request", side_effect=lambda _: httpx.Response(429)), patch("app.crawler.fetcher.time.sleep"), self.assertRaises(RateLimitError):
                fetcher.get(ROOT)
            self.assertEqual(fetcher.requests, 2)
            self.assertFalse(fetcher.cache)
        finally:
            fetcher.close()

    def test_crawl_preserves_pages_and_stops_following_requests(self):
        fetcher = FakeFetcher({"/about": RateLimitError("HTTP 429")})
        result = Crawler(CrawlConfig(delay=0), fetcher).crawl(ROOT)
        self.assertEqual([page.url for page in result.pages], [ROOT])
        self.assertNotIn(ROOT + "docs/", fetcher.calls)
        self.assertEqual(result.errors, ["HTTP 429"])

    def test_sitemap_rate_limit_does_not_continue_to_homepage(self):
        fetcher = FakeFetcher({"/sitemap.xml": RateLimitError("HTTP 429")})
        with self.assertRaises(RateLimitError):
            Crawler(CrawlConfig(delay=0), fetcher).crawl(ROOT)
        self.assertNotIn(ROOT, fetcher.calls)

    def test_filter_artifacts_keeps_download_overview_and_roadmap(self):
        self.assertFalse(relevant(ROOT + "download/6.1.2/tarball/"))
        self.assertFalse(relevant(ROOT + "download/6.1.2/checksum/"))
        self.assertTrue(relevant(ROOT + "download/"))
        self.assertTrue(relevant(ROOT + "download/6.2/roadmap/"))

    def test_invalid_delays(self):
        for delay in (-1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                CrawlConfig(delay=delay)
