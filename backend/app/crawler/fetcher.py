"""Pin every connection to a checked public IP; preserve hostname for TLS/SNI.

No environment proxies, automatic redirects, cookies, or DNS second lookup.
"""
import time
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import httpx
from .urls import normalize_url, public_addresses, in_scope, UnsafeURL

log = logging.getLogger(__name__)

class RateLimitError(ValueError):
    """Stop this crawl rather than continuing requests against a throttled site."""

def retry_after_seconds(value, now=None):
    if value is None:
        return 5.0
    if value.strip().isdigit():
        return float(value.strip())
    try:
        when = parsedate_to_datetime(value)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (when - (now or datetime.now(timezone.utc))).total_seconds())
    except (ValueError, TypeError, OverflowError):
        return 5.0

class Fetcher:
    def __init__(self, config, start):
        self.config, self.start = config, start
        self.transport = httpx.HTTPTransport(retries=0)
        self.last_request = 0.0
        self.cache = {}
        self.requests = 0
        self.minimum_delay = config.delay

    def close(self):
        self.transport.close()

    def get(self, url, allowed=None):
        allowed = allowed or (lambda u: in_scope(u, self.start))
        aliases=[]
        redirects = 0
        retried = False
        while redirects <= 5:
            url = normalize_url(url)
            if not allowed(url):
                raise UnsafeURL("Redirect or request outside allowed origin")
            if url in self.cache:
                cached=self.cache[url]
                if not allowed(str(cached.url)):
                    raise UnsafeURL("Cached redirect outside allowed scope")
                return cached
            aliases.append(url)
            ip = public_addresses(url)[0]
            wait = self.minimum_delay - (time.monotonic()-self.last_request)
            if wait > 0: time.sleep(wait)
            original = httpx.URL(url)
            request = httpx.Request("GET", original.copy_with(host=ip), headers={"Host":original.netloc.decode(), "User-Agent":self.config.user_agent, "Accept-Encoding":"identity"}, extensions={"sni_hostname":original.host, "timeout":dict.fromkeys(("connect","read","write","pool"), self.config.timeout)})
            self.last_request = time.monotonic()
            self.requests += 1
            response = self.transport.handle_request(request)
            try:
                if response.status_code == 429:
                    wait = retry_after_seconds(response.headers.get("retry-after"))
                    if retried or wait > self.config.max_retry_after:
                        raise RateLimitError(f"HTTP 429 at {url}; crawl stopped. Retry-After wait: {wait:g}s. Try again later.")
                    retried = True
                    # Slow subsequent requests too; a successful retry does not
                    # imply the original request rate has become acceptable.
                    self.minimum_delay = max(self.minimum_delay, 2.0)
                    log.warning("HTTP 429 at %s; waiting %gs before one retry", url, wait)
                    response.close()
                    time.sleep(max(wait, self.minimum_delay))
                    continue
                if response.status_code in (301,302,303,307,308):
                    location=response.headers.get("location")
                    if not location: raise ValueError("Redirect lacks location")
                    url=normalize_url(location,url)
                    redirects += 1
                    continue
                chunks=[]; size=0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > self.config.max_bytes: raise ValueError("Response exceeds size limit")
                    chunks.append(chunk)
                result = httpx.Response(response.status_code, headers=response.headers, content=b"".join(chunks), request=httpx.Request("GET", url))
                if response.status_code < 400:
                    for alias in aliases: self.cache[alias]=result
                return result
            finally:
                response.close()
        raise ValueError("Too many redirects")
