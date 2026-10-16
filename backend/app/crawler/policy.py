"""Per-origin robots checks for the submitted host and its bare/www alias."""
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from .urls import in_crawl_scope, site_origin_allowed
from .fetcher import RateLimitError


class CrawlPolicy:
    def __init__(self, fetcher, start, config):
        self.fetcher, self.start, self.config = fetcher, start, config
        self.robots = {}
        self.failures = {}

    def rules(self, url):
        p = urlsplit(url)
        origin = (p.scheme, p.netloc)
        if origin in self.failures:
            raise ValueError(self.failures[origin])
        if origin not in self.robots:
            robots_url = urlunsplit((*origin, '/robots.txt', '', ''))
            try:
                response = self.fetcher.get(robots_url, allowed=lambda u: site_origin_allowed(u, self.start))
                if response.status_code in (404, 410):
                    text = ''
                elif 200 <= response.status_code < 300:
                    text = response.text
                else:
                    raise ValueError(f'robots.txt returned {response.status_code}')
                parser = RobotFileParser()
                parser.parse(text.splitlines())
                self.robots[origin] = (parser, text)
                delay = parser.crawl_delay(self.config.user_agent) or 0
                rate = parser.request_rate(self.config.user_agent)
                if hasattr(self.fetcher, 'minimum_delay'):
                    self.fetcher.minimum_delay = max(self.fetcher.minimum_delay, self.config.delay, delay,
                                                     rate.seconds / rate.requests if rate and rate.requests else 0)
            except RateLimitError:
                raise
            except Exception as exc:
                self.failures[origin] = f'Cannot establish robots.txt permission: {exc}'
                raise ValueError(f'Cannot establish robots.txt permission: {exc}') from exc
        return self.robots[origin]

    def can_fetch(self, url):
        if not site_origin_allowed(url, self.start):
            return False
        parser, _ = self.rules(url)
        return parser.can_fetch(self.config.user_agent, url)

    def page_allowed(self, url):
        return (in_crawl_scope(url, self.start, self.config.crawl_scope, allow_www=True)
                and self.can_fetch(url))
