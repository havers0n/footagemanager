from __future__ import annotations

import csv
import io
import json
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from .db import database
from .services.worker import index_worker

router = APIRouter(prefix='/api/vehicles')


class VehicleLabel(BaseModel):
    label: str = Field(min_length=1, max_length=120)


@router.post('/scan')
def scan(asset_id: int | None = None, force: bool = False):
    if asset_id is not None:
        with database() as conn:
            if not conn.execute("SELECT 1 FROM assets WHERE id=? AND media_type='video' AND status='ready'", (asset_id,)).fetchone():
                raise HTTPException(404, 'Processed video not found')
    return {'queued': index_worker.enqueue_vehicles(asset_id, force)}


@router.get('')
def listing(asset_id: int | None = None, q: str = '', group_id: str | None = None,
            archived: bool = False, offset: int = Query(0, ge=0), limit: int = Query(60, ge=1, le=200)):
    where = """WHERE (? IS NULL OR v.asset_id=?) AND (v.label LIKE ? OR a.filename LIKE ?)
               AND v.archived=? AND (? IS NULL OR v.group_id=?)"""
    params = (asset_id, asset_id, f'%{q}%', f'%{q}%', int(archived), group_id, group_id)
    with database() as conn:
        total = conn.execute(f'SELECT COUNT(*) FROM vehicle_crops v JOIN assets a ON a.id=v.asset_id {where}', params).fetchone()[0]
        rows = conn.execute(f'''SELECT v.*,a.filename,a.path source_video,
                               (SELECT COUNT(*) FROM vehicle_crops g WHERE g.group_id=v.group_id AND g.archived=v.archived) group_size
                               FROM vehicle_crops v JOIN assets a ON a.id=v.asset_id {where}
                               ORDER BY v.asset_id,v.timestamp_ms,v.id LIMIT ? OFFSET ?''', (*params, limit, offset)).fetchall()
        videos = conn.execute("SELECT id,filename FROM assets WHERE media_type='video' AND status='ready' ORDER BY filename").fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item['has_frame'] = bool(item.pop('frame_path'))
        item.pop('image_path')
        for key in ('bbox_json', 'crop_bbox_json', 'quality_json', 'similarity_json'):
            item[key.removesuffix('_json')] = json.loads(item.pop(key) or 'null')
        items.append(item)
    return {'total': total, 'items': items, 'videos': [dict(r) for r in videos]}


@router.get('/export')
def export(asset_id: int | None = None):
    with database() as conn:
        rows = conn.execute('''SELECT v.*,a.filename,a.path source_video FROM vehicle_crops v JOIN assets a ON a.id=v.asset_id
                               WHERE v.archived=0 AND (? IS NULL OR v.asset_id=?) ORDER BY v.asset_id,v.timestamp_ms''', (asset_id, asset_id)).fetchall()
    if not rows:
        raise HTTPException(404, 'No photos to download')
    with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as temporary:
        path = Path(temporary.name)
    try:
        with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_STORED) as archive:
            metadata = io.StringIO()
            writer = csv.writer(metadata)
            writer.writerow(['photo', 'full_frame', 'source_video', 'timestamp_ms', 'track_id', 'scene', 'start_ms', 'end_ms', 'label', 'confidence', 'bbox_xyxy', 'crop_bbox_xyxy', 'quality', 'possible_group', 'similarities'])
            for row in rows:
                image = Path(row['image_path'])
                if not image.is_file():
                    raise HTTPException(409, 'Photo unavailable. Wait for processing to finish and try downloading again.')
                name = f"video-{row['asset_id']}/auto-{row['id']:05d}-{row['timestamp_ms']}ms.jpg"
                archive.write(image, name)
                frame_name = ''
                if row['frame_path']:
                    frame = Path(row['frame_path'])
                    if not frame.is_file():
                        raise HTTPException(409, 'Original frame unavailable')
                    frame_name = f"video-{row['asset_id']}/auto-{row['id']:05d}-{row['timestamp_ms']}ms-frame.jpg"
                    archive.write(frame, frame_name)
                writer.writerow([name, frame_name, row['source_video'], row['timestamp_ms'], row['track_id'],
                                 row['scene_number'], row['start_ms'], row['end_ms'], row['label'], row['confidence'],
                                 row['bbox_json'], row['crop_bbox_json'], row['quality_json'], row['group_id'], row['similarity_json']])
            archive.writestr('catalog.csv', '\ufeff' + metadata.getvalue())
        return FileResponse(path, media_type='application/zip', filename='automobiles.zip', background=BackgroundTask(path.unlink, missing_ok=True))
    except Exception:
        path.unlink(missing_ok=True)
        raise


@router.get('/{vehicle_id}/image')
def image(vehicle_id: int, download: bool = False):
    with database() as conn:
        row = conn.execute('SELECT image_path FROM vehicle_crops WHERE id=?', (vehicle_id,)).fetchone()
    if not row or not Path(row['image_path']).is_file():
        raise HTTPException(404, 'Photo unavailable')
    return FileResponse(row['image_path'], media_type='image/jpeg', filename=f'auto-{vehicle_id}.jpg' if download else None)


@router.get('/{vehicle_id}/frame')
def original_frame(vehicle_id: int):
    with database() as conn:
        row = conn.execute('SELECT frame_path FROM vehicle_crops WHERE id=?', (vehicle_id,)).fetchone()
    if not row or not row['frame_path'] or not Path(row['frame_path']).is_file():
        raise HTTPException(404, 'Original frame unavailable. Run track processing for legacy results.')
    return FileResponse(row['frame_path'], media_type='image/jpeg')


@router.put('/{vehicle_id}')
def label(vehicle_id: int, payload: VehicleLabel):
    cleaned = payload.label.strip()
    if not cleaned:
        raise HTTPException(422, 'Enter a label')
    with database() as conn:
        if not conn.execute('UPDATE vehicle_crops SET label=? WHERE id=?', (cleaned, vehicle_id)).rowcount:
            raise HTTPException(404, 'Photo not found')
    return {'label': cleaned}


@router.delete('/{vehicle_id}')
def remove(vehicle_id: int):
    with database() as conn:
        row = conn.execute('SELECT image_path,frame_path FROM vehicle_crops WHERE id=?', (vehicle_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'Photo not found')
        conn.execute('DELETE FROM vehicle_crops WHERE id=?', (vehicle_id,))
    Path(row['image_path']).unlink(missing_ok=True)
    if row['frame_path']:
        Path(row['frame_path']).unlink(missing_ok=True)
    return {'removed': True}
