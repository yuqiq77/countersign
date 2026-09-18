"""FastAPI application factory."""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from countersign.api.routes import router
from countersign.contracts import HealthResponse

_DEFAULT_CORS_ORIGINS = "http://localhost:3001,http://127.0.0.1:3001"


def cors_origins() -> list[str]:
    """Allowed browser origins, configurable per deployment."""

    raw = os.environ.get("COUNTERSIGN_CORS_ORIGINS", _DEFAULT_CORS_ORIGINS)
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def healthcheck() -> HealthResponse:
    """Return process liveness without probing downstream dependencies."""

    return HealthResponse()


def create_app() -> FastAPI:
    """Create the API without starting background work at import time."""

    application = FastAPI(
        title="Countersign",
        version="0.1.0",
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins(),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.add_api_route(
        "/healthz",
        healthcheck,
        methods=["GET"],
        response_model=HealthResponse,
        tags=["operations"],
    )
    application.include_router(router)

    return application


app = create_app()
