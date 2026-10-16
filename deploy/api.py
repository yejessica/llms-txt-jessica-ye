"""Deployment-only submission limit; the existing backend stays reusable.

A shared token bucket bounds ALL demo submissions, independent of forwarded
headers. GET polling/download/health requests never consume submission tokens.
Run exactly one Uvicorn process so this limit has one authoritative bucket.
"""
import math
import os
import re
import time

from app.api.main import app
from starlette.responses import JSONResponse


class SubmissionLimit:
    def __init__(self, app):
        self.app = app
        self.rate = int(os.environ.get("LLMS_SUBMISSIONS_PER_MINUTE", "6")) / 60
        self.capacity = int(os.environ.get("LLMS_SUBMISSION_BURST", "3"))
        if self.rate <= 0 or self.capacity < 1:
            raise ValueError("Submission rate and burst must be positive")
        self.tokens = float(self.capacity)
        self.updated = time.monotonic()

    async def __call__(self, scope, receive, send):
        if (scope["type"] == "http" and scope["method"] == "POST"
                and re.fullmatch(r"/api/(generate|websites/[^/]+/refresh)/?", scope["path"])):
            now = time.monotonic()
            self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
            self.updated = now
            if self.tokens < 1:
                retry = math.ceil((1 - self.tokens) / self.rate)
                response = JSONResponse(
                    {"error": {"code": "rate_limited", "message": "Demo is busy. Try again shortly."}},
                    status_code=429, headers={"Retry-After": str(retry), "Cache-Control": "no-store"},
                )
                return await response(scope, receive, send)
            self.tokens -= 1
        await self.app(scope, receive, send)


app.add_middleware(SubmissionLimit)
