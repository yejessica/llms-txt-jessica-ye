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

    def __post_init__(self):
        if self.crawl_scope not in {"directory", "site"}:
            raise ValueError("crawl-scope must be directory or site")
        if not 1 <= self.max_pages <= 1000 or not 0 <= self.max_depth <= 20:
            raise ValueError("max-pages must be 1–1000 and max-depth 0–20")
        if any(not math.isfinite(value) or value < 0 for value in (self.delay, self.max_retry_after)):
            raise ValueError("Request delay and retry wait must be finite and nonnegative")
