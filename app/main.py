"""FastAPI web application for config management."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware

from app.core import scheduler
from app.core.config_manager import ConfigManager
from app.core.runtime_settings import load_runtime_settings
from app.core.log_manager import (
    WebSocketLogHandler,
    log_broadcaster,
    log_manager,
    set_log_loop,
)
from app.core.security import SECRET_KEY, set_secret_key
from tg_media_dedupe_bot.db import Database
from tg_media_dedupe_bot.pipeline_runtime import PipelineRuntime

from app.routers import (
    auth,
    chat_effective_state,
    cleaner,
    config as config_router,
    filters,
    forwarding,
    logs,
    media_filter,
    session,
    settings,
    providers,
    models,
    tags,
    tools,
    users,
    ai_health,
    tag_cleanup,
    telegram_controllers,
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
        version="4.0",
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
    chat_effective_state.set_config_manager(config_manager)
    config_router.set_config_manager(config_manager)
    media_filter.set_config_manager(config_manager)
    settings.set_config_manager(config_manager)
    providers.set_config_manager(config_manager)
    models.set_config_manager(config_manager)
    session.set_config_manager(config_manager)
    forwarding.set_config_manager(config_manager)
    tools.set_config_manager(config_manager)
    users.set_config_manager(config_manager)
    telegram_controllers.set_config_manager(config_manager)
    ai_health.set_config_manager(config_manager)
    tag_cleanup.set_config_manager(config_manager)

    runtime_settings = load_runtime_settings(config_manager.get_config())
    db_config = config_manager.get_config().get("database", {})
    db_path = db_config.get("path", "./data/bot.db")
    cleaner_runtime = PipelineRuntime(Database(Path(db_path)), worker_count=runtime_settings.pipeline_worker_count)
    cleaner.set_pipeline_runtime(cleaner_runtime)
    cleaner.register_runtime_executors(cleaner_runtime)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if reload_hook is not None:
            config_manager.register_reload_hook(reload_hook)
        await cleaner_runtime.start()
        await scheduler.start_scheduler(config_manager.get_config())
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
        try:
            yield
        finally:
            if hasattr(app.state, "log_task"):
                app.state.log_task.cancel()
                try:
                    await app.state.log_task
                except asyncio.CancelledError:
                    pass
            handler = getattr(app.state, "log_handler", None)
            if handler is not None:
                logging.getLogger().removeHandler(handler)
            await cleaner_runtime.shutdown()
            await scheduler.shutdown_scheduler()

    app.router.lifespan_context = lifespan

    # Include routers
    app.include_router(auth.router)
    app.include_router(auth.token_router)
    app.include_router(cleaner.router)
    app.include_router(chat_effective_state.router)
    app.include_router(config_router.router)
    app.include_router(tags.router)
    app.include_router(filters.router)
    app.include_router(media_filter.router)
    app.include_router(settings.router)
    app.include_router(providers.router)
    app.include_router(models.router)
    app.include_router(session.router)
    app.include_router(forwarding.router)
    app.include_router(tools.router)
    app.include_router(users.router)
    app.include_router(telegram_controllers.router)
    app.include_router(logs.router)
    app.include_router(ai_health.router)
    app.include_router(tag_cleanup.router)


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

    @app.get("/task_center", response_class=HTMLResponse)
    async def task_center_page(request: Request) -> HTMLResponse:
        """Serve unified task center page."""
        return templates.TemplateResponse(
            "task_center.html",
            {"request": request, "active_page": "task_center"},
        )

    @app.get("/review_queue", response_class=HTMLResponse)
    async def review_queue_page(request: Request) -> HTMLResponse:
        """Serve review queue page."""
        return templates.TemplateResponse(
            "review_queue.html",
            {"request": request, "active_page": "review_queue"},
        )

    @app.get("/dead_letters", response_class=HTMLResponse)
    async def dead_letters_page(request: Request) -> HTMLResponse:
        """Serve dead-letter queue page."""
        return templates.TemplateResponse(
            "dead_letters.html",
            {"request": request, "active_page": "dead_letters"},
        )


    @app.get("/tag_cleanup", response_class=HTMLResponse)
    async def tag_cleanup_page(request: Request) -> HTMLResponse:
        """Serve tag cleanup page."""
        return templates.TemplateResponse(
            "tag_cleanup.html",
            {"request": request, "active_page": "tag_cleanup"},
        )

    @app.get("/chat_visibility", response_class=HTMLResponse)
    async def chat_visibility_page(request: Request) -> HTMLResponse:
        """Serve chat visibility diagnostics page."""
        return templates.TemplateResponse(
            "chat_visibility.html",
            {"request": request, "active_page": "chat_visibility"},
        )

    @app.get("/users", response_class=HTMLResponse)
    async def users_page(request: Request) -> HTMLResponse:
        """Serve web admin users page."""
        return templates.TemplateResponse(
            "users.html",
            {"request": request, "active_page": "users"},
        )

    @app.get("/telegram_controllers", response_class=HTMLResponse)
    async def telegram_controllers_page(request: Request) -> HTMLResponse:
        """Serve telegram controllers page."""
        return templates.TemplateResponse(
            "telegram_controllers.html",
            {"request": request, "active_page": "telegram_controllers"},
        )

    @app.get("/account", response_class=HTMLResponse)
    async def account_page(request: Request) -> HTMLResponse:
        """Serve account management page as an alias of users page."""
        return templates.TemplateResponse(
            "users.html",
            {"request": request, "active_page": "account"},
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


    return app
