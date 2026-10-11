from __future__ import annotations

from bisect import bisect_right
import json
import shutil
import uuid
from pathlib import Path
from typing import Callable

import cv2

from ..config import settings
from ..db import database
from .vehicle_tracking import VehicleTracker, crop_box, descriptor, group_similar, overlap, padded_box, quality

PIPELINE_VERSION = 2
LABELS = {2: 'Car', 5: 'Bus', 7: 'Truck'}


def sampled_frames(source, interval, cancelled):
    """Sequential decoding with true source PTS, including variable-frame-rate video."""
    import av
    with av.open(str(source)) as container:
        stream = container.streams.video[0]
        stream.thread_count = 2
        next_ms = 0
        origin = float(stream.start_time or 0) * float(stream.time_base)
        for decoded in container.decode(stream):
            if cancelled():
                raise InterruptedError('Vehicle processing stopped')
            if decoded.pts is None:
                raise RuntimeError('Video frame has no timestamp')
            timestamp = max(0, round((float(decoded.pts * decoded.time_base) - origin) * 1000))
            if timestamp < next_ms:
                continue
            next_ms = timestamp + interval
            yield timestamp, decoded.to_ndarray(format='bgr24')


def extract_vehicles(asset: dict, job_id: int, progress: Callable, cancelled: Callable) -> int:
    from ultralytics import YOLO

    source = Path(asset['path'])
    if not source.is_file():
        raise FileNotFoundError('Source video unavailable')
    model_path = settings.data_dir / 'models' / 'yolo11n.pt'
    model_path.parent.mkdir(parents=True, exist_ok=True)
    progress(.01, 'Loading vehicle detector and tracker')
    model = YOLO(str(model_path))
    run_id = f"v{asset['id']}-j{job_id}-{uuid.uuid4().hex[:8]}"
    output = settings.cache_dir / str(asset['id']) / 'vehicles' / run_id
    output.mkdir(parents=True, exist_ok=True)
    records = {}
    interval = max(100, settings.vehicle_track_sample_ms)
    duration = int(asset.get('duration_ms') or 0)
    with database() as conn:
        boundaries = [r[0] for r in conn.execute('SELECT start_ms FROM scenes WHERE asset_id=? ORDER BY start_ms', (asset['id'],))]
        annotations = [dict(r) for r in conn.execute('SELECT * FROM vehicle_crops WHERE asset_id=? AND archived=0', (asset['id'],))
                       if r['label'] not in LABELS.values()]
    previous_scene, tracker, frame_count = -1, None, 0
    try:
        for timestamp, frame in sampled_frames(source, interval, cancelled):
            frame_count += 1
            scene = bisect_right(boundaries, timestamp)
            if scene != previous_scene:
                tracker = VehicleTracker(interval, settings.vehicle_track_gap_ms, settings.vehicle_confidence)
            previous_scene = scene
            height, width = frame.shape[:2]
            # People and other object boxes are retained only for the occlusion estimate.
            result = model.predict(frame, conf=.10, imgsz=640, verbose=False, device='cpu')[0]
            all_boxes = list(zip(result.boxes.xyxy.tolist(), result.boxes.conf.tolist(), result.boxes.cls.tolist()))
            vehicles = [(box, conf, int(category), i) for i, (box, conf, category) in enumerate(all_boxes)
                        if int(category) in LABELS and crop_box(box, width, height)]
            detections = [[*box, conf, category] for box, conf, category, _ in vehicles]
            for local_id, detection_index, confirmed in tracker.update(detections, frame):
                box, confidence, category, all_index = vehicles[detection_index]
                bounds = crop_box(box, width, height)
                track_id = f'{run_id}-s{scene}-t{local_id}'
                if track_id not in records:
                    records[track_id] = {'track_id': track_id, 'scene_number': scene,
                        'category': category, 'start_ms': timestamp, 'quality_score': -1, 'confirmed': False,
                        'image_path': str(output / f's{scene}-t{local_id}-crop.jpg'),
                        'frame_path': str(output / f's{scene}-t{local_id}-frame.jpg'),
                        'label': LABELS[category]}
                record = records[track_id]
                record['end_ms'] = timestamp
                record['confirmed'] = record['confirmed'] or confirmed
                for annotation in annotations:
                    if abs(annotation['timestamp_ms'] - timestamp) <= interval and overlap(bounds, json.loads(annotation['bbox_json'])) > .4:
                        record['label'] = annotation['label']
                others = [b for i, (b, conf, _) in enumerate(all_boxes) if i != all_index and conf >= .35]
                score, metrics = quality(frame, bounds, confidence, others)
                if score > record['quality_score']:
                    crop_bounds = padded_box(bounds, width, height, min(.15, max(.10, settings.vehicle_crop_padding)))
                    x1, y1, x2, y2 = crop_bounds
                    crop = frame[y1:y2, x1:x2]
                    for path, image in ((record['image_path'], crop), (record['frame_path'], frame)):
                        if not cv2.imwrite(path, image, [cv2.IMWRITE_JPEG_QUALITY, 95]):
                            raise RuntimeError('Could not save vehicle frame')
                    bx1, by1, bx2, by2 = bounds
                    record.update(timestamp_ms=timestamp, confidence=float(confidence), bbox_json=json.dumps(bounds),
                        crop_bbox_json=json.dumps(crop_bounds), quality_score=score, quality_json=json.dumps(metrics),
                        descriptor=descriptor(frame[by1:by2, bx1:bx2]))
            if frame_count % 25 == 0:
                progress(.03 + .90 * min(1, timestamp / max(1, duration)),
                         f'Tracks: {len(records)} · {timestamp // 1000}/{duration // 1000} s')
        if frame_count == 0:
            raise RuntimeError('No frames available in the video')
        if cancelled():
            raise InterruptedError('Vehicle processing stopped')
        # Unconfirmed one-frame proposals are not final ByteTrack tracks.
        for key in [key for key, record in records.items() if not record['confirmed']]:
            rejected = records.pop(key)
            Path(rejected['image_path']).unlink(missing_ok=True)
            Path(rejected['frame_path']).unlink(missing_ok=True)
        progress(.95, 'Grouping similar vehicles across scenes')
        group_similar(list(records.values()))
        with database() as conn:
            if not conn.execute('SELECT 1 FROM assets WHERE id=? AND quick_hash=?', (asset['id'], asset['quick_hash'])).fetchone():
                raise RuntimeError('Video changed or deleted during processing')
            # Archive earlier runs including all manual labels. Similarity grouping never removes a track.
            conn.execute('UPDATE vehicle_crops SET archived=1 WHERE asset_id=?', (asset['id'],))
            for record in records.values():
                columns = ['timestamp_ms', 'end_ms', 'confidence', 'bbox_json', 'image_path', 'label', 'track_id',
                           'scene_number', 'start_ms', 'frame_path', 'crop_bbox_json', 'quality_score', 'quality_json', 'group_id']
                conn.execute(f'''INSERT INTO vehicle_crops(asset_id,pipeline_version,similarity_json,{','.join(columns)})
                                 VALUES ({','.join('?' for _ in range(len(columns) + 3))})''',
                             [asset['id'], PIPELINE_VERSION, json.dumps(record['similarity_json'])] + [record[c] for c in columns])
        return len(records)
    except Exception:
        shutil.rmtree(output, ignore_errors=True)
        raise
