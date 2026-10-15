from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


Status = Literal['pending', 'running', 'completed', 'failed']


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    url: str = Field(min_length=1, max_length=2048)


class JobCreated(BaseModel):
    job_id: str
    website_id: str
    status: Status


class Job(JobCreated):
    job_type: Literal['initial', 'scheduled']
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    pages_crawled: int
    pages_selected: int
    error: str | None
    warnings: list[str]
    output_changed: bool | None


class GenerationInfo(BaseModel):
    generation_id: str
    content_hash: str
    generated_at: datetime
    is_latest: bool


class Website(BaseModel):
    website_id: str
    original_url: str
    normalized_url: str
    title: str | None
    created_at: datetime
    last_crawl_at: datetime | None
    last_successful_generation_at: datetime | None
    next_check_at: datetime | None
    status: Status
    latest_generation: GenerationInfo | None
