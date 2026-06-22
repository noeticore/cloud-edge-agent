"""FastAPI application entrypoint.

Usage:
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.dependencies.deps import create_components
from app.api.routers import chat, documents, health
from app.core.config.settings import get_settings
from app.core.exceptions.exceptions import BaseAppException
from app.core.logger.logger import get_logger, setup_logging

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — initialize components on startup."""
    settings = get_settings()
    setup_logging(settings.log_level)

    components = await create_components()
    app.state.components = components

    yield

    # Cleanup (close connections, flush caches, etc.)
    components.cache.clear()


def create_app() -> FastAPI:
    """Application factory."""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        description="Privacy-First Cloud-Edge Collaborative AI Agent",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Global exception handlers
    @app.exception_handler(BaseAppException)
    async def app_exception_handler(
        request: Request, exc: BaseAppException
    ) -> JSONResponse:
        """Handle application-specific exceptions with structured JSON."""
        logger.warning("app_exception", error=exc.message, details=exc.details)
        return JSONResponse(
            status_code=400,
            content={"error": exc.message, "detail": str(exc.details)},
        )

    @app.exception_handler(Exception)
    async def global_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        """Catch-all handler for unexpected errors."""
        logger.error("unhandled_exception", error=str(exc), exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error", "detail": str(exc)},
        )

    # Routers
    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(documents.router)

    return app


app = create_app()
