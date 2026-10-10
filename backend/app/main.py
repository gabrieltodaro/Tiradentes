"""FastAPI application entrypoint for Tiradentes V1 MVP."""

from fastapi import FastAPI

from app.api.v1.router import router as v1_router


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    app = FastAPI(
        title="Tiradentes API",
        version="0.1.0",
        description="MVP API for checking whether a CPF belongs to a PEP profile.",
    )
    app.include_router(v1_router, prefix="/api/v1", tags=["v1"])
    return app


app = create_app()
