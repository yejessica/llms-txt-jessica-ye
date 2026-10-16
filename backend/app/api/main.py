from contextlib import asynccontextmanager
import logging
import socket
import sqlite3

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException

from ..crawler.urls import normalize_url, public_addresses, UnsafeURL
from ..database import Database, NotFound
from ..settings import Settings
from .schemas import GenerateRequest, GenerationInfo, Job, JobCreated, Website

log = logging.getLogger(__name__)


def create_app(settings=None):
    settings = settings or Settings.from_env()
    database = Database(settings.db_path)

    @asynccontextmanager
    async def lifespan(app):
        database.initialize()
        yield

    app = FastAPI(title="Automated llms.txt Generator", version="0.2.0", lifespan=lifespan)
    app.state.database = database

    @app.exception_handler(NotFound)
    async def not_found(request, exc):
        return JSONResponse(status_code=404, content={"error": {"code": "not_found", "message": str(exc)}})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse(status_code=422, content={"error": {"code": "invalid_request", "message": "Provide a JSON object containing a URL string of 1–2048 characters."}})

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": "http_error", "message": str(exc.detail)}})

    @app.exception_handler(sqlite3.OperationalError)
    async def database_error(request, exc):
        log.exception("Database unavailable")
        return JSONResponse(status_code=503, content={"error": {"code": "database_unavailable", "message": "Database temporarily unavailable. Try again shortly."}})

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        log.exception("Unhandled API error")
        return JSONResponse(status_code=500, content={"error": {"code": "internal_error", "message": "An unexpected server error occurred."}})

    @app.post("/api/generate", response_model=JobCreated, status_code=202)
    def submit(body: GenerateRequest):
        try:
            normalized = normalize_url(body.url)
            # Reject unsafe destinations before persistence; the crawler checks DNS
            # again and pins each connection, including redirects, at execution time.
            public_addresses(normalized)
        except (UnsafeURL, ValueError, socket.gaierror, OSError):
            return JSONResponse(status_code=422, content={"error": {"code": "invalid_url", "message": "URL must resolve to a public HTTP(S) website without credentials or nonstandard ports."}})
        return database.submit(body.url, normalized)

    @app.get("/api/jobs/{job_id}", response_model=Job)
    def job(job_id: str):
        return database.get_job(job_id)

    @app.get("/api/websites/{website_id}", response_model=Website)
    def website(website_id: str):
        return database.get_website(website_id)

    @app.get("/api/websites/{website_id}/llms.txt", response_class=PlainTextResponse)
    def markdown(website_id: str):
        return PlainTextResponse(database.latest(website_id)['markdown'])

    @app.get("/api/websites/{website_id}/download", response_class=PlainTextResponse)
    def download(website_id: str):
        return PlainTextResponse(database.latest(website_id)['markdown'], headers={
            "Content-Disposition": 'attachment; filename="llms.txt"',
            "X-Content-Type-Options": "nosniff",
        })

    @app.post("/api/websites/{website_id}/refresh", response_model=JobCreated, status_code=202)
    def refresh(website_id: str):
        return database.refresh(website_id)

    @app.get("/api/websites/{website_id}/generations", response_model=list[GenerationInfo])
    def generations(website_id: str):
        return database.generations(website_id)

    @app.get("/api/websites/{website_id}/generations/{generation_id}", response_class=PlainTextResponse)
    def generation(website_id: str, generation_id: str):
        return PlainTextResponse(database.generation(website_id, generation_id)['markdown'])

    @app.get("/api/health")
    def health():
        database.health()
        return {"status": "ok"}

    return app


app = create_app()
