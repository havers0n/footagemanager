import cv2
import numpy as np
from fastapi.testclient import TestClient

from app.db import database, initialize_database
from app.main import app
from app.services.vehicle_tracking import VehicleTracker, descriptor, group_similar, padded_box, quality, occlusion_fraction
from app.services.worker import IndexWorker
from test_vehicles import seed


def test_byte_tracker_retains_id_across_motion_low_confidence_and_missing_frame():
    tracker = VehicleTracker(200, 2000, .45)
    frame = np.zeros((200, 400, 3), np.uint8)
    ids = []
    for n in range(6):
        box = [20 + n * 8, 40, 120 + n * 8, 130, .2 if n == 3 else .9, 2]
        tracks = tracker.update([] if n == 4 else [box], frame)
        if n != 4:
            assert len(tracks) == 1
            ids.append(tracks[0][0])
    assert len(set(ids)) == 1


def test_simultaneous_cars_have_separate_tracks():
    tracker = VehicleTracker(200, 2000, .45)
    frame = np.zeros((200, 400, 3), np.uint8)
    for n in range(5):
        tracks = tracker.update([[10+n*4, 20, 90+n*4, 100, .9, 2], [220-n*4, 40, 300-n*4, 120, .8, 2]], frame)
        assert len({t[0] for t in tracks}) == 2


def test_expired_track_does_not_reuse_id():
    tracker = VehicleTracker(200, 400, .45)
    frame = np.zeros((160, 200, 3), np.uint8)
    box = [[20, 20, 120, 100, .9, 2]]
    first = tracker.update(box, frame)[0][0]
    for _ in range(5):
        tracker.update([], frame)
    assert tracker.update(box, frame)[0][0] != first


def test_quality_rewards_sharp_complete_unoccluded_confident_large_crop():
    frame = np.random.default_rng(42).integers(0, 255, (400, 400, 3), dtype=np.uint8)
    box = [100, 100, 300, 300]
    good, _ = quality(frame, box, .9, [])
    assert good > quality(cv2.GaussianBlur(frame, (25, 25), 8), box, .9, [])[0]
    assert good > quality(frame, box, .4, [])[0]
    assert good > quality(frame, box, .9, [[100, 100, 250, 300]])[0]
    assert good > quality(frame, [0, 100, 200, 300], .9, [])[0]
    assert good > quality(frame, [100, 100, 160, 160], .9, [])[0]
    assert occlusion_fraction(box, [box, box]) == 1
    assert padded_box([100, 100, 300, 300], 400, 400) == [75, 75, 325, 325]
    assert padded_box([0, 0, 100, 100], 110, 110) == [0, 0, 110, 110]


def test_cross_scene_groups_retain_every_track_and_exclude_simultaneous_cars():
    image = np.random.default_rng(12).integers(0, 255, (80, 120, 3), dtype=np.uint8)
    records = [{'track_id': str(i), 'scene_number': scene, 'category': 2, 'descriptor': descriptor(image)}
               for i, scene in enumerate([1, 2, 1])]
    group_similar(records)
    assert len(records) == 3
    assert records[0]['group_id'] == records[1]['group_id'] is not None
    assert records[2]['group_id'] is None
    assert records[0]['similarity_json'][0]['track_id'] == '1'


def test_frame_group_metadata_history_and_force_rebuild(isolated_data_dir):
    asset, vehicle, image = seed(isolated_data_dir)
    frame = isolated_data_dir / 'full.jpg'
    frame.write_bytes(b'full-frame')
    with database() as conn:
        conn.execute('''UPDATE vehicle_crops SET track_id='test-track',frame_path=?,group_id='possible-test',
                       pipeline_version=2,quality_json='{"sharpness":80}',crop_bbox_json='[0,0,110,110]' WHERE id=?''', (str(frame), vehicle))
        conn.execute('''INSERT INTO vehicle_crops(asset_id,timestamp_ms,end_ms,confidence,bbox_json,image_path,group_id,track_id)
                       VALUES (?,900,1000,.8,'[0,0,100,100]',?,'possible-test','second-track')''', (asset, str(image)))
    client = TestClient(app)
    assert client.get(f'/api/vehicles/{vehicle}/frame').content == b'full-frame'
    items = client.get('/api/vehicles?group_id=possible-test').json()['items']
    assert len(items) == 2
    assert items[0]['group_size'] == 2
    assert items[0]['source_video'] == '/missing/car.mp4'
    assert items[0]['bbox'] == [0, 0, 100, 100]
    assert items[0]['has_frame']
    with database() as conn:
        conn.execute('UPDATE vehicle_crops SET archived=1 WHERE id=?', (vehicle,))
    assert client.get('/api/vehicles').json()['total'] == 1
    assert client.get('/api/vehicles?archived=true').json()['total'] == 1
    worker = IndexWorker()
    assert worker.enqueue_vehicles(asset) == 1
    with database() as conn:
        conn.execute("UPDATE jobs SET status='completed'")
    assert worker.enqueue_vehicles(asset) == 0
    assert worker.enqueue_vehicles(asset, force=True) == 1
    assert worker.enqueue_vehicles(asset, force=True) == 0
    initialize_database()
    assert client.get('/api/vehicles?archived=true').json()['items'][0]['track_id'] == 'test-track'


def test_unconfirmed_proposal_is_not_published_but_prior_labels_remain(isolated_data_dir, monkeypatch):
    from types import SimpleNamespace
    import ultralytics
    from app.services import vehicles
    asset_id, _, source = seed(isolated_data_dir)
    with database() as conn:
        conn.execute('UPDATE assets SET path=?,duration_ms=1000 WHERE id=?', (str(source), asset_id))
        asset = dict(conn.execute('SELECT * FROM assets WHERE id=?', (asset_id,)).fetchone())
    class Detector:
        def __init__(self, path):
            self.calls = 0
        def predict(self, *args, **kwargs):
            self.calls += 1
            rows = [[20, 20, 120, 100]] if self.calls == 2 else []
            return [SimpleNamespace(boxes=SimpleNamespace(xyxy=np.array(rows).reshape(-1, 4),
                    conf=np.array([.9] if rows else []), cls=np.array([2] if rows else [])))]
    monkeypatch.setattr(ultralytics, 'YOLO', Detector)
    monkeypatch.setattr(vehicles, 'sampled_frames', lambda *args: iter(
        [(0, np.zeros((160, 200, 3), np.uint8)), (200, np.zeros((160, 200, 3), np.uint8)),
         (400, np.zeros((160, 200, 3), np.uint8))]))
    assert vehicles.extract_vehicles(asset, 700, lambda *args: None, lambda: False) == 0
    with database() as conn:
        assert conn.execute('SELECT COUNT(*) FROM vehicle_crops WHERE archived=0').fetchone()[0] == 0
        assert conn.execute('SELECT label FROM vehicle_crops WHERE archived=1').fetchone()[0] == 'Автомобиль'


def test_job_claim_is_atomic(isolated_data_dir):
    from concurrent.futures import ThreadPoolExecutor
    asset, _, _ = seed(isolated_data_dir)
    worker = IndexWorker()
    worker.enqueue_vehicles(asset)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: worker._next_job(), range(2)))
    assert sum(result is not None for result in results) == 1
