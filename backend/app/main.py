from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import router
from .vehicle_api import router as vehicle_router
from .config import settings
from .db import initialize_database
from .services.worker import index_worker


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.ensure_directories()
    initialize_database()
    index_worker.start()
    yield
    index_worker.stop()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
app.include_router(vehicle_router)

frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
assets_dir = frontend_dist / "assets"
if assets_dir.is_dir():
    app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")


@app.get("/{path:path}", include_in_schema=False)
def frontend(path: str):
    index = frontend_dist / "index.html"
    if index.is_file():
        return FileResponse(index)
    return {"message": "Frontend is not built. Run npm install && npm run build in frontend/."}
