from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse

from .config import settings
from .db import database
from .schemas import ExportRequest, ScreenshotRequest, SearchResponse, SourceCreate, SourceOut, TagUpdate
from .services.catalog import add_source, scan_source
from .services.media import export_clip, export_frame, safe_export_name, safe_screenshot_name
from .services.search import search_assets
from .services.worker import index_worker


router = APIRouter(prefix="/api")


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "app": settings.app_name}


@router.get("/capabilities")
def capabilities() -> dict:
    from .services.semantic import semantic_index

    try:
        import whisperx  # noqa: F401
        transcription_available = settings.transcription_enabled
    except ImportError:
        transcription_available = False

    return {
        "ffmpeg": bool(shutil.which("ffmpeg") and shutil.which("ffprobe")),
        "semantic_search": semantic_index.available,
        "transcription": transcription_available,
        "gpu": _gpu_name(),
    }


def _gpu_name() -> str | None:
    try:
        import torch
        return torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
    except ImportError:
        return None


@router.get("/sources", response_model=list[SourceOut])
def list_sources() -> list[dict]:
    with database() as conn:
        rows = conn.execute(
            """SELECT s.*,COUNT(a.id) asset_count FROM sources s
               LEFT JOIN assets a ON a.source_id=s.id GROUP BY s.id ORDER BY s.name"""
        ).fetchall()
        return [{**dict(row), "enabled": bool(row["enabled"])} for row in rows]


@router.post("/sources", response_model=SourceOut, status_code=201)
def create_source(payload: SourceCreate) -> dict:
    try:
        source_id = add_source(payload.path, payload.name)
        scan_source(source_id)
        index_worker.wake()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with database() as conn:
        row = conn.execute(
            "SELECT s.*,(SELECT COUNT(*) FROM assets a WHERE a.source_id=s.id) asset_count FROM sources s WHERE s.id=?",
            (source_id,),
        ).fetchone()
        return {**dict(row), "enabled": bool(row["enabled"])}


@router.post("/sources/{source_id}/scan")
def rescan_source(source_id: int) -> dict:
    try:
        summary = scan_source(source_id)
        index_worker.wake()
        return summary.__dict__
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/sources/{source_id}")
def delete_source(source_id: int) -> dict:
    with database() as conn:
        source = conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")
        asset_ids = [row[0] for row in conn.execute("SELECT id FROM assets WHERE source_id=?", (source_id,))]
        conn.execute("DELETE FROM sources WHERE id=?", (source_id,))
    for asset_id in asset_ids:
        cache = settings.cache_dir / str(asset_id)
        if cache.is_dir() and cache.parent == settings.cache_dir:
            shutil.rmtree(cache)
    return {"removed": True, "originals_untouched": True}


@router.get("/jobs")
def list_jobs(limit: int = Query(100, ge=1, le=500)) -> dict:
    with database() as conn:
        rows = conn.execute(
            """SELECT j.*,a.filename FROM jobs j LEFT JOIN assets a ON a.id=j.asset_id
               ORDER BY CASE j.status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END,j.id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        counts = {row["status"]: row["count"] for row in conn.execute("SELECT status,COUNT(*) count FROM jobs GROUP BY status")}
    return {"paused": index_worker.paused(), "counts": counts, "jobs": [dict(row) for row in rows]}


@router.post("/jobs/pause")
def pause_jobs() -> dict:
    index_worker.set_paused(True)
    return {"paused": True}


@router.post("/jobs/resume")
def resume_jobs() -> dict:
    index_worker.set_paused(False)
    return {"paused": False}


@router.post("/jobs/retry")
def retry_jobs() -> dict:
    return {"retried": index_worker.retry_failed()}


@router.post("/jobs/reindex-visual")
def reindex_visual_jobs() -> dict:
    return {"queued": index_worker.enqueue_visual_reindex()}


@router.post("/jobs/reindex-hud")
def reindex_hud_jobs() -> dict:
    return {"queued": index_worker.enqueue_hud_reindex()}


@router.post("/jobs/reindex-ui")
def reindex_ui_jobs() -> dict:
    return {"queued": index_worker.enqueue_ui_reindex()}


@router.get("/events")
async def events() -> StreamingResponse:
    async def stream():
        last_payload = ""
        while True:
            data = list_jobs(20)
            payload = json.dumps(data, ensure_ascii=False, default=str)
            if payload != last_payload:
                yield f"event: jobs\ndata: {payload}\n\n"
                last_payload = payload
            else:
                yield ": keepalive\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.get("/search", response_model=SearchResponse)
def search(
    q: str = "",
    media_type: list[str] = Query(default=[]),
    source_id: int | None = None,
    min_duration_ms: int | None = Query(default=None, ge=0),
    max_duration_ms: int | None = Query(default=None, ge=0),
    orientation: str | None = None,
    has_speech: bool | None = None,
    limit: int = Query(60, ge=1, le=200),
) -> dict:
    allowed = {"video", "image", "audio"}
    if not set(media_type).issubset(allowed):
        raise HTTPException(status_code=422, detail="Unsupported media type")
    return search_assets(q, media_type, source_id, min_duration_ms, max_duration_ms, orientation, has_speech, limit)


def _asset(asset_id: int):
    with database() as conn:
        row = conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        return dict(row) if row else None


@router.get("/assets/{asset_id}")
def asset_detail(asset_id: int) -> dict:
    asset = _asset(asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    with database() as conn:
        scenes = [dict(row) for row in conn.execute("SELECT * FROM scenes WHERE asset_id=? ORDER BY scene_number", (asset_id,))]
        transcripts = [dict(row) for row in conn.execute("SELECT id,start_ms,end_ms,text,language FROM transcripts WHERE asset_id=? ORDER BY start_ms", (asset_id,))]
        tags = [row[0] for row in conn.execute("SELECT t.name FROM tags t JOIN asset_tags at ON at.tag_id=t.id WHERE at.asset_id=? ORDER BY t.name", (asset_id,))]
    return {**asset, "scenes": scenes, "transcripts": transcripts, "tags": tags}


@router.get("/assets/{asset_id}/thumbnail")
def asset_thumbnail(asset_id: int) -> FileResponse:
    asset = _asset(asset_id)
    path = Path(asset["thumbnail_path"]) if asset and asset.get("thumbnail_path") else None
    if not path or not path.is_file():
        raise HTTPException(status_code=404, detail="Thumbnail unavailable")
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "public,max-age=86400"})


@router.get("/scenes/{scene_id}/thumbnail")
def scene_thumbnail(scene_id: int) -> FileResponse:
    with database() as conn:
        row = conn.execute("SELECT thumbnail_path FROM scenes WHERE id=?", (scene_id,)).fetchone()
    path = Path(row["thumbnail_path"]) if row and row["thumbnail_path"] else None
    if not path or not path.is_file():
        raise HTTPException(status_code=404, detail="Scene thumbnail unavailable")
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "public,max-age=86400"})


@router.get("/frames/{frame_id}/thumbnail")
def frame_thumbnail(frame_id: int) -> FileResponse:
    with database() as conn:
        row = conn.execute("SELECT thumbnail_path FROM scene_frames WHERE id=?", (frame_id,)).fetchone()
    path = Path(row["thumbnail_path"]) if row and row["thumbnail_path"] else None
    if not path or not path.is_file():
        raise HTTPException(status_code=404, detail="Frame thumbnail unavailable")
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "public,max-age=86400"})


@router.get("/hud/{detection_id}/thumbnail")
def hud_thumbnail(detection_id: int) -> FileResponse:
    with database() as conn:
        row = conn.execute("SELECT thumbnail_path FROM hud_detections WHERE id=?", (detection_id,)).fetchone()
    path = Path(row["thumbnail_path"]) if row and row["thumbnail_path"] else None
    if not path or not path.is_file():
        raise HTTPException(status_code=404, detail="HUD thumbnail unavailable")
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "public,max-age=86400"})


@router.get("/assets/{asset_id}/media")
def asset_media(asset_id: int, original: bool = False) -> FileResponse:
    asset = _asset(asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    selected = asset["path"] if original or not asset.get("preview_path") else asset["preview_path"]
    path = Path(selected)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Media unavailable")
    return FileResponse(path)


@router.put("/assets/{asset_id}/tags")
def update_tags(asset_id: int, payload: TagUpdate) -> dict:
    cleaned = sorted({tag.strip() for tag in payload.tags if tag.strip()})
    with database() as conn:
        if not conn.execute("SELECT 1 FROM assets WHERE id=?", (asset_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Asset not found")
        conn.execute("DELETE FROM asset_tags WHERE asset_id=?", (asset_id,))
        conn.execute("DELETE FROM search_fts WHERE entity_type='tag' AND asset_id=?", (asset_id,))
        for tag in cleaned:
            conn.execute("INSERT OR IGNORE INTO tags(name) VALUES (?)", (tag,))
            tag_id = conn.execute("SELECT id FROM tags WHERE name=? COLLATE NOCASE", (tag,)).fetchone()[0]
            conn.execute("INSERT INTO asset_tags(asset_id,tag_id) VALUES (?,?)", (asset_id, tag_id))
            conn.execute("INSERT INTO search_fts(entity_type,entity_id,asset_id,text) VALUES ('tag',?,?,?)", (tag_id, asset_id, tag))
    return {"tags": cleaned}


@router.post("/exports", status_code=201)
async def create_export(payload: ExportRequest) -> dict:
    asset = _asset(payload.asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    if asset["media_type"] == "image":
        raise HTTPException(status_code=422, detail="Images cannot be trimmed")
    if payload.end_ms <= payload.start_ms:
        raise HTTPException(status_code=422, detail="End time must be after start time")
    if asset.get("duration_ms") and payload.end_ms > asset["duration_ms"] + 50:
        raise HTTPException(status_code=422, detail="End time exceeds media duration")
    output = settings.export_dir / safe_export_name(Path(asset["filename"]).stem, payload.start_ms, payload.end_ms)
    with database() as conn:
        cursor = conn.execute(
            "INSERT INTO exports(asset_id,start_ms,end_ms,mode,output_path,status) VALUES (?,?,?,?,?,'running')",
            (payload.asset_id, payload.start_ms, payload.end_ms, payload.mode, str(output)),
        )
        export_id = int(cursor.lastrowid)
    try:
        await asyncio.to_thread(export_clip, Path(asset["path"]), output, payload.start_ms, payload.end_ms, payload.mode)
        with database() as conn:
            conn.execute("UPDATE exports SET status='completed' WHERE id=?", (export_id,))
    except Exception as exc:
        with database() as conn:
            conn.execute("UPDATE exports SET status='failed',error=? WHERE id=?", (str(exc)[-4000:], export_id))
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "id": export_id,
        "status": "completed",
        "output_path": str(output),
        "download_url": f"/api/exports/{export_id}/download",
    }


@router.get("/exports")
def list_exports() -> list[dict]:
    with database() as conn:
        return [dict(row) for row in conn.execute("SELECT e.*,a.filename FROM exports e JOIN assets a ON a.id=e.asset_id ORDER BY e.id DESC LIMIT 100")]


@router.get("/exports/{export_id}/download")
def download_export(export_id: int) -> FileResponse:
    with database() as conn:
        row = conn.execute("SELECT output_path,status FROM exports WHERE id=?", (export_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Export not found")
    if row["status"] != "completed":
        raise HTTPException(status_code=409, detail="Export is not ready")
    path = Path(row["output_path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Exported file is unavailable")
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@router.post("/screenshots", status_code=201)
async def create_screenshot(payload: ScreenshotRequest) -> dict:
    asset = _asset(payload.asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    if asset["media_type"] != "video":
        raise HTTPException(status_code=422, detail="A frame can only be captured from video")
    if asset.get("duration_ms") and payload.timestamp_ms > asset["duration_ms"]:
        raise HTTPException(status_code=422, detail="Frame time exceeds video duration")
    with database() as conn:
        screenshot_id = int(conn.execute(
            "INSERT INTO screenshots(asset_id,timestamp_ms,output_path,status) VALUES (?,?,?,'running')",
            (payload.asset_id, payload.timestamp_ms, ""),
        ).lastrowid)
        output = settings.export_dir / safe_screenshot_name(
            Path(asset["filename"]).stem, payload.timestamp_ms, screenshot_id,
        )
        conn.execute("UPDATE screenshots SET output_path=? WHERE id=?", (str(output), screenshot_id))
    try:
        await asyncio.to_thread(export_frame, Path(asset["path"]), output, payload.timestamp_ms)
        with database() as conn:
            conn.execute("UPDATE screenshots SET status='completed' WHERE id=?", (screenshot_id,))
    except Exception as exc:
        with database() as conn:
            conn.execute(
                "UPDATE screenshots SET status='failed',error=? WHERE id=?",
                (str(exc)[-4000:], screenshot_id),
            )
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "id": screenshot_id,
        "status": "completed",
        "timestamp_ms": payload.timestamp_ms,
        "output_path": str(output),
        "download_url": f"/api/screenshots/{screenshot_id}/download",
    }


@router.get("/screenshots/{screenshot_id}/download")
def download_screenshot(screenshot_id: int) -> FileResponse:
    with database() as conn:
        row = conn.execute("SELECT output_path,status FROM screenshots WHERE id=?", (screenshot_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Screenshot not found")
    if row["status"] != "completed":
        raise HTTPException(status_code=409, detail="Screenshot is not ready")
    path = Path(row["output_path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Screenshot file is unavailable")
    return FileResponse(path, media_type="image/png", filename=path.name)
