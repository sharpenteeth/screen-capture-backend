import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import database
from app.config import Settings
from app.routers import agent, auth, requests, screenshots, users
from app.seed import seed
from app.socket import agent_socket
from app.storage import Storage
from app.ws import Hub

logger = logging.getLogger("screencapture")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.load()
    database.init_db(settings.database_url)
    app.state.settings = settings
    app.state.storage = Storage.open(settings.data_dir)
    app.state.hub = Hub()
    db = database.SessionLocal()
    try:
        seed(db)
    finally:
        db.close()
    logger.info("backend ready at %s", settings.data_dir)
    yield
    if database.engine is not None:
        database.engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="Screen Capture", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(screenshots.router)
    app.include_router(requests.router)
    app.include_router(agent.router)
    app.websocket("/ws/agent")(agent_socket)

    @app.get("/")
    def root() -> dict:
        return {"service": "screen-capture", "docs": "/docs"}

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True}

    return app


app = create_app()
