from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import database
from app.main import app


def test_health_and_empty_library():
    with TestClient(app) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        assert client.get("/api/sources").json() == []
        response = client.get("/api/search")
        assert response.status_code == 200
        assert response.json()["results"] == []


def test_rejects_missing_source():
    with TestClient(app) as client:
        response = client.post("/api/sources", json={"path": "/definitely/not/here"})
        assert response.status_code == 400


def test_download_completed_export(isolated_data_dir):
    output = isolated_data_dir / "exports" / "clip.mp4"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"video")
    with database() as conn:
        source_id = conn.execute("INSERT INTO sources(path,name) VALUES (?,?)", ("/library", "Library")).lastrowid
        asset_id = conn.execute(
            """INSERT INTO assets(source_id,path,relative_path,filename,media_type,size_bytes,mtime_ns,quick_hash,status)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (source_id, "/library/source.mp4", "source.mp4", "source.mp4", "video", 1, 1, "hash", "ready"),
        ).lastrowid
        export_id = conn.execute(
            "INSERT INTO exports(asset_id,start_ms,end_ms,mode,output_path,status) VALUES (?,?,?,?,?,?)",
            (asset_id, 0, 1_000, "accurate", str(output), "completed"),
        ).lastrowid

    with TestClient(app) as client:
        response = client.get(f"/api/exports/{export_id}/download")

    assert response.status_code == 200
    assert response.content == b"video"
    assert 'filename="clip.mp4"' in response.headers["content-disposition"]


def test_create_and_download_screenshot(isolated_data_dir, monkeypatch):
    source_file = isolated_data_dir / "source.mp4"
    source_file.write_bytes(b"video")
    with database() as conn:
        source_id = conn.execute("INSERT INTO sources(path,name) VALUES (?,?)", (str(isolated_data_dir), "Library")).lastrowid
        asset_id = conn.execute(
            """INSERT INTO assets(source_id,path,relative_path,filename,media_type,size_bytes,mtime_ns,quick_hash,status,duration_ms)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (source_id, str(source_file), "source.mp4", "source.mp4", "video", 1, 1, "hash", "ready", 2_000),
        ).lastrowid

    def fake_export_frame(source, output, timestamp_ms):
        assert source == source_file
        assert timestamp_ms == 1_234
        output.write_bytes(b"png")

    monkeypatch.setattr("app.api.export_frame", fake_export_frame)
    with TestClient(app) as client:
        created = client.post("/api/screenshots", json={"asset_id": asset_id, "timestamp_ms": 1_234})
        assert created.status_code == 201
        payload = created.json()
        assert payload["output_path"].endswith("source_frame_000001234_1.png")
        downloaded = client.get(payload["download_url"])

    assert downloaded.status_code == 200
    assert downloaded.content == b"png"
    assert downloaded.headers["content-type"] == "image/png"
