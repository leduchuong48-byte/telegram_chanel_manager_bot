"""FastAPI web application for config management."""

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware

from app.core import scheduler
from app.core.config_manager import ConfigManager
from app.core.log_manager import (
    WebSocketLogHandler,
    log_broadcaster,
    log_manager,
    set_log_loop,
)
from app.core.security import SECRET_KEY, set_secret_key
from app.routers import (
    auth,
    cleaner,
    config as config_router,
    filters,
    forwarding,
    logs,
    media_filter,
    session,
    settings,
    tags,
    tools,
    users,
)

logger = logging.getLogger(__name__)


def create_app(
    config_manager: ConfigManager,
    reload_hook: Callable[[dict[str, Any]], Any] | None = None,
) -> FastAPI:
    """Create and configure FastAPI application."""
    
    # Initialize security settings
    web_config = config_manager.get_config().get("web_admin", {})
    secret_key = web_config.get("secret_key", "change-this-in-production")
    set_secret_key(secret_key)
    
    # Create FastAPI app
    app = FastAPI(
        title="Admin Panel API",
        description="Configuration management API for Telegram Media Dedup Bot",
        version="3.0",
    )

    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Set config manager for routers
    auth.set_config_manager(config_manager)
    cleaner.set_config_manager(config_manager)
    config_router.set_config_manager(config_manager)
    media_filter.set_config_manager(config_manager)
    settings.set_config_manager(config_manager)
    session.set_config_manager(config_manager)
    forwarding.set_config_manager(config_manager)
    tools.set_config_manager(config_manager)
    users.set_config_manager(config_manager)

    # Include routers
    app.include_router(auth.router)
    app.include_router(auth.token_router)
    app.include_router(cleaner.router)
    app.include_router(config_router.router)
    app.include_router(tags.router)
    app.include_router(filters.router)
    app.include_router(media_filter.router)
    app.include_router(settings.router)
    app.include_router(session.router)
    app.include_router(forwarding.router)
    app.include_router(tools.router)
    app.include_router(users.router)
    app.include_router(logs.router)

    if reload_hook is not None:
        @app.on_event("startup")
        async def _register_reload_hook() -> None:
            config_manager.register_reload_hook(reload_hook)

    @app.on_event("startup")
    async def _start_scheduler() -> None:
        await scheduler.start_scheduler(config_manager.get_config())

    @app.on_event("shutdown")
    async def _shutdown_scheduler() -> None:
        await scheduler.shutdown_scheduler()

    # Mount static files
    static_path = Path(__file__).parent / "static"
    if static_path.exists():
        app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

    # HTML routes
    templates_path = Path(__file__).parent / "templates"
    templates = Jinja2Templates(directory=str(templates_path))

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request) -> HTMLResponse:
        """Serve login page."""
        return templates.TemplateResponse("login.html", {"request": request})

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request) -> HTMLResponse:
        """Serve dashboard page."""
        return templates.TemplateResponse(
            "dashboard.html",
            {"request": request, "active_page": "dashboard"},
        )

    @app.get("/bot_settings", response_class=HTMLResponse)
    async def bot_settings_page(request: Request) -> HTMLResponse:
        """Serve bot settings page."""
        return templates.TemplateResponse(
            "bot_settings.html",
            {"request": request, "active_page": "bot_settings"},
        )

    @app.get("/session", response_class=HTMLResponse)
    async def session_page(request: Request) -> HTMLResponse:
        """Serve Telegram session login page."""
        return templates.TemplateResponse(
            "login_telegram.html",
            {"request": request, "active_page": "session"},
        )

    @app.get("/forwarding", response_class=HTMLResponse)
    async def forwarding_page(request: Request) -> HTMLResponse:
        """Serve forwarding rules page."""
        return templates.TemplateResponse(
            "forwarding.html",
            {"request": request, "active_page": "forwarding"},
        )

    @app.get("/tags", response_class=HTMLResponse)
    async def tags_page(request: Request) -> HTMLResponse:
        """Serve tags manager placeholder page."""
        return templates.TemplateResponse(
            "tags.html",
            {"request": request, "active_page": "tags"},
        )

    @app.get("/cleaner", response_class=HTMLResponse)
    async def cleaner_page(request: Request) -> HTMLResponse:
        """Serve cleaner management page."""
        return templates.TemplateResponse(
            "cleaner.html",
            {"request": request, "active_page": "cleaner"},
        )

    @app.get("/users", response_class=HTMLResponse)
    async def users_page(request: Request) -> HTMLResponse:
        """Serve web admin users page."""
        return templates.TemplateResponse(
            "users.html",
            {"request": request, "active_page": "users"},
        )

    @app.get("/tools", response_class=HTMLResponse)
    async def tools_page(request: Request) -> HTMLResponse:
        """Serve maintenance tools page."""
        return templates.TemplateResponse(
            "tools.html",
            {"request": request, "active_page": "tools"},
        )

    @app.get("/filters", response_class=HTMLResponse)
    async def filters_page(request: Request) -> HTMLResponse:
        """Serve filters manager placeholder page."""
        return templates.TemplateResponse(
            "filters.html",
            {"request": request, "active_page": "filters"},
        )

    @app.get("/media_filter", response_class=HTMLResponse)
    async def media_filter_page(request: Request) -> HTMLResponse:
        """Serve media filter manager page."""
        return templates.TemplateResponse(
            "media_filter.html",
            {"request": request, "active_page": "media_filter"},
        )

    @app.get("/config_editor", response_class=HTMLResponse)
    async def config_editor(request: Request) -> HTMLResponse:
        """Serve configuration editor page."""
        return templates.TemplateResponse(
            "config_editor.html",
            {"request": request, "active_page": "config_editor"},
        )

    @app.get("/logs", response_class=HTMLResponse)
    async def logs_page(request: Request) -> HTMLResponse:
        """Serve logs viewer page."""
        return templates.TemplateResponse(
            "logs.html",
            {"request": request, "active_page": "logs"},
        )

    @app.get("/health")
    async def health_check() -> dict:
        """Health check endpoint."""
        return {"status": "ok", "service": "admin-panel"}

    @app.on_event("startup")
    async def _setup_log_streaming() -> None:
        set_log_loop(asyncio.get_running_loop())
        root_logger = logging.getLogger()
        handler: WebSocketLogHandler | None = None
        for current in root_logger.handlers:
            if isinstance(current, WebSocketLogHandler):
                handler = current
                break
        if handler is None:
            handler = WebSocketLogHandler()
            root_logger.addHandler(handler)
        app.state.log_handler = handler

        for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            target_logger = logging.getLogger(logger_name)
            target_logger.propagate = True

        app.state.log_task = asyncio.create_task(log_broadcaster(log_manager))

    @app.on_event("shutdown")
    async def _shutdown_log_streaming() -> None:
        if hasattr(app.state, "log_task"):
            app.state.log_task.cancel()
            try:
                await app.state.log_task
            except asyncio.CancelledError:
                pass
        handler = getattr(app.state, "log_handler", None)
        if handler is not None:
            logging.getLogger().removeHandler(handler)

    return app
