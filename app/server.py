"""FastAPI application factory: mounts the /api router and the static frontend."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from app.api import router as api_router

STATIC_DIR = Path(__file__).resolve().parent / "static"


class RevalidatingStaticFiles(StaticFiles):
    """Static files the browser must revalidate before each use.

    There is no build step to fingerprint file names, so without this a
    browser keeps running an old app.js/view after an update. Revalidation
    is a cheap 304 (StaticFiles sends ETag / Last-Modified) on localhost.
    """

    def file_response(self, *args: Any, **kwargs: Any) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def create_app() -> FastAPI:
    app = FastAPI(title="Voice Practice Coach")

    # Registered first: /api/* must never be shadowed by the static catch-all.
    app.include_router(api_router)

    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/", RevalidatingStaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    return app


app = create_app()
