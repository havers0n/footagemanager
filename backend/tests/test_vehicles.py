import io
import zipfile

from fastapi.testclient import TestClient

from app.db import database
from app.main import app
from app.services.vehicles import crop_box, overlap
from app.services.worker import IndexWorker


def seed(tmp_path):
    photo = tmp_path / 'car.jpg'
    photo.write_bytes(b'jpeg-test')
    with database() as conn:
        source = conn.execute("INSERT INTO sources(path,name,enabled) VALUES ('/missing','Test',0)").lastrowid
        asset = conn.execute("""INSERT INTO assets(source_id,path,relative_path,filename,media_type,size_bytes,mtime_ns,quick_hash,status)
            VALUES (?,'/missing/car.mp4','car.mp4','car.mp4','video',1,1,'h','ready')""", (source,)).lastrowid
        vehicle = conn.execute("""INSERT INTO vehicle_crops(asset_id,timestamp_ms,end_ms,confidence,bbox_json,image_path)
            VALUES (?,500,1000,0.9,'[0,0,100,100]',?)""", (asset, str(photo))).lastrowid
    return asset, vehicle, photo


def test_clamp_crop_and_overlap():
    assert crop_box([-20, -10, 130, 80], 100, 60) == (0, 0, 100, 60)
    assert crop_box([10, 10, 20, 20], 100, 60) is None
    assert crop_box([100, 100, 0, 0], 100, 60) is None
    assert overlap([0, 0, 100, 100], [0, 0, 100, 100]) == 1
    assert overlap([0, 0, 10, 10], [20, 20, 30, 30]) == 0


def test_labels_export_and_removal(isolated_data_dir):
    asset, vehicle, photo = seed(isolated_data_dir)
    client = TestClient(app)
    assert client.get('/api/vehicles').json()['total'] == 1
    assert client.put(f'/api/vehicles/{vehicle}', json={'label': '  Машина GTA  '}).json()['label'] == 'Машина GTA'
    assert client.put(f'/api/vehicles/{vehicle}', json={'label': '  '}).status_code == 422
    assert client.get('/api/vehicles?q=GTA').json()['total'] == 1
    assert client.get('/api/vehicles?asset_id=999').json()['total'] == 0
    assert client.get(f'/api/vehicles/{vehicle}/image').content == b'jpeg-test'
    response = client.get(f'/api/vehicles/export?asset_id={asset}')
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert 'Машина GTA' in archive.read('catalog.csv').decode('utf-8-sig')
        assert len(archive.namelist()) == 2
    assert client.delete(f'/api/vehicles/{vehicle}').status_code == 200
    assert not photo.exists()
    assert client.get('/api/vehicles').json()['total'] == 0
    assert client.get('/api/vehicles/export').status_code == 404
    assert client.get(f'/api/vehicles/{vehicle}/image').status_code == 404


def test_queue_does_not_duplicate_or_overwrite_completed(isolated_data_dir):
    asset, _, _ = seed(isolated_data_dir)
    worker = IndexWorker()
    assert worker.enqueue_vehicles(asset) == 1
    assert worker.enqueue_vehicles(asset) == 0
    with database() as conn:
        conn.execute("UPDATE jobs SET status='completed' WHERE kind='vehicles_v2'")
    assert worker.enqueue_vehicles(asset) == 0


def test_extraction_merges_neighbors_and_preserves_previous_on_failure(isolated_data_dir, monkeypatch):
    import sys
    from types import SimpleNamespace
    import cv2
    import numpy as np
    import pytest
    from app.services.vehicles import extract_vehicles

    asset_id, old_id, _ = seed(isolated_data_dir)
    video = isolated_data_dir / 'test.mp4'
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'mp4v'), 2, (160, 120))
    for _ in range(3):
        writer.write(np.full((120, 160, 3), 100, dtype=np.uint8))
    writer.release()
    with database() as conn:
        conn.execute('UPDATE assets SET path=?,duration_ms=1500 WHERE id=?', (str(video), asset_id))
        asset = dict(conn.execute('SELECT * FROM assets WHERE id=?', (asset_id,)).fetchone())
    class FakeModel:
        def __init__(self, path):
            self.calls = 0
        def predict(self, *args, **kwargs):
            self.calls += 1
            confidence = .9 if self.calls == 2 else .6
            return [SimpleNamespace(boxes=SimpleNamespace(xyxy=np.array([[10, 10, 110, 100]]), conf=np.array([confidence]), cls=np.array([2])))]
    import ultralytics
    monkeypatch.setattr(ultralytics, 'YOLO', FakeModel)
    assert extract_vehicles(asset, 999, lambda *args: None, lambda: False) == 1
    with database() as conn:
        row = conn.execute('SELECT * FROM vehicle_crops WHERE asset_id=? AND archived=0', (asset_id,)).fetchone()
        assert row['timestamp_ms'] == 500
        assert row['end_ms'] == 1000
        assert row['track_id']
        assert row['frame_path']
        assert row['crop_bbox_json'] != row['bbox_json']
        assert row['id'] != old_id
        current_id = row['id']
    with pytest.raises(InterruptedError):
        extract_vehicles(asset, 1000, lambda *args: None, lambda: True)
    with database() as conn:
        assert conn.execute('SELECT id FROM vehicle_crops WHERE asset_id=? AND archived=0', (asset_id,)).fetchone()[0] == current_id
