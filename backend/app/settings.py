"""Shared API/worker configuration. Environment is read at process startup."""
import os
from typing import Literal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .config import CrawlConfig


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    db_path: Path = Path(__file__).resolve().parents[1] / "data" / "llms.sqlite3"
    check_interval: float = Field(default=86400, ge=1)
    poll_interval: float = Field(default=2, ge=0.05)
    job_timeout: float = Field(default=300, gt=0)
    max_pages: int = Field(default=40, ge=1, le=1000)
    max_depth: int = Field(default=3, ge=0, le=20)
    request_timeout: float = Field(default=15, gt=0)
    request_delay: float = Field(default=1, ge=0)
    crawl_scope: Literal["directory", "site"] = "directory"
    max_sitemap_bytes: int = Field(default=52_428_800, ge=1, le=52_428_800)
    max_sitemap_compressed_bytes: int = Field(default=10_485_760, ge=1, le=52_428_800)
    max_sitemap_entries: int = Field(default=50_000, ge=1, le=50_000)
    max_sitemap_total_bytes: int = Field(default=67_108_864, ge=1, le=536_870_912)
    sitemap_timeout: float = Field(default=30, gt=0)
    max_query_variants: int = Field(default=5, ge=1, le=100)

    @classmethod
    def from_env(cls):
        return cls(**{
            name: os.environ[f"LLMS_{name.upper()}"]
            for name in cls.model_fields if f"LLMS_{name.upper()}" in os.environ
        })

    def crawl_config(self):
        return CrawlConfig(max_pages=self.max_pages, max_depth=self.max_depth,
                           timeout=self.request_timeout, delay=self.request_delay,
                           crawl_scope=self.crawl_scope, max_sitemap_bytes=self.max_sitemap_bytes,
                           max_sitemap_compressed_bytes=self.max_sitemap_compressed_bytes,
                           max_sitemap_entries=self.max_sitemap_entries,
                           max_sitemap_total_bytes=self.max_sitemap_total_bytes,
                           sitemap_timeout=self.sitemap_timeout,
                           max_query_variants=self.max_query_variants)
