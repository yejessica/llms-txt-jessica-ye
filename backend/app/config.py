from dataclasses import dataclass
import math

@dataclass(frozen=True)
class CrawlConfig:
    max_pages: int = 40
    max_depth: int = 3
    timeout: float = 15
    delay: float = 1.0
    max_retry_after: float = 30
    max_bytes: int = 2_000_000
    max_sitemaps: int = 12
    max_discovered: int = 5000
    user_agent: str = "LlmsTxtGenerator/0.1"
    crawl_scope: str = "directory"
    max_sitemap_bytes: int = 52_428_800
    max_sitemap_compressed_bytes: int = 10_485_760
    max_sitemap_entries: int = 50_000
    max_sitemap_total_bytes: int = 67_108_864
    sitemap_timeout: float = 30
    max_query_variants: int = 5

    def __post_init__(self):
        if self.crawl_scope not in {"directory", "site"}:
            raise ValueError("crawl-scope must be directory or site")
        if any(value < 1 for value in (self.max_sitemap_bytes, self.max_sitemap_compressed_bytes,
                                       self.max_sitemap_entries, self.max_sitemap_total_bytes, self.max_query_variants)):
            raise ValueError("Sitemap and query limits must be positive")
        if not math.isfinite(self.sitemap_timeout) or self.sitemap_timeout <= 0:
            raise ValueError("Sitemap timeout must be finite and positive")
        if not 1 <= self.max_pages <= 1000 or not 0 <= self.max_depth <= 20:
            raise ValueError("max-pages must be 1–1000 and max-depth 0–20")
        if any(not math.isfinite(value) or value < 0 for value in (self.delay, self.max_retry_after)):
            raise ValueError("Request delay and retry wait must be finite and nonnegative")
