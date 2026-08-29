"""FastAPI application factory."""

from fastapi import FastAPI

from countersign.contracts import HealthResponse


def healthcheck() -> HealthResponse:
    """Return process liveness without probing downstream dependencies."""

    return HealthResponse()


def create_app() -> FastAPI:
    """Create the API without starting background work at import time."""

    application = FastAPI(
        title="Countersign",
        version="0.1.0",
    )
    application.add_api_route(
        "/healthz",
        healthcheck,
        methods=["GET"],
        response_model=HealthResponse,
        tags=["operations"],
    )

    return application


app = create_app()
