from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import settings


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_scan_at TEXT,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS assets (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    path TEXT NOT NULL UNIQUE,
    relative_path TEXT NOT NULL,
    filename TEXT NOT NULL,
    media_type TEXT NOT NULL CHECK(media_type IN ('video','image','audio')),
    size_bytes INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    quick_hash TEXT NOT NULL,
    duration_ms INTEGER,
    width INTEGER,
    height INTEGER,
    fps REAL,
    has_audio INTEGER NOT NULL DEFAULT 0,
    has_speech INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    thumbnail_path TEXT,
    preview_path TEXT,
    error TEXT,
    indexed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_assets_source ON assets(source_id);
CREATE INDEX IF NOT EXISTS idx_assets_type ON assets(media_type);
CREATE INDEX IF NOT EXISTS idx_assets_status ON assets(status);

CREATE TABLE IF NOT EXISTS scenes (
    id INTEGER PRIMARY KEY,
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    scene_number INTEGER NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    thumbnail_path TEXT,
    UNIQUE(asset_id, scene_number)
);
CREATE INDEX IF NOT EXISTS idx_scenes_asset ON scenes(asset_id);

CREATE TABLE IF NOT EXISTS scene_frames (
    id INTEGER PRIMARY KEY,
    scene_id INTEGER NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    timestamp_ms INTEGER NOT NULL,
    thumbnail_path TEXT NOT NULL,
    UNIQUE(scene_id, timestamp_ms)
);
CREATE INDEX IF NOT EXISTS idx_scene_frames_asset ON scene_frames(asset_id);
CREATE INDEX IF NOT EXISTS idx_scene_frames_scene ON scene_frames(scene_id);

CREATE TABLE IF NOT EXISTS hud_detections (
    id INTEGER PRIMARY KEY,
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    scene_id INTEGER NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    star_count INTEGER NOT NULL,
    confidence REAL NOT NULL,
    thumbnail_path TEXT,
    UNIQUE(asset_id, start_ms, star_count)
);
CREATE INDEX IF NOT EXISTS idx_hud_detections_asset ON hud_detections(asset_id);
CREATE INDEX IF NOT EXISTS idx_hud_detections_stars ON hud_detections(star_count, confidence);

CREATE TABLE IF NOT EXISTS vehicle_crops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    timestamp_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    confidence REAL NOT NULL,
    bbox_json TEXT NOT NULL,
    image_path TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT 'Car'
);
CREATE INDEX IF NOT EXISTS idx_vehicle_crops_asset ON vehicle_crops(asset_id);

CREATE TABLE IF NOT EXISTS transcripts (
    id INTEGER PRIMARY KEY,
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    text TEXT NOT NULL,
    language TEXT,
    words_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_transcripts_asset ON transcripts(asset_id);

CREATE TABLE IF NOT EXISTS audio_segments (
    id INTEGER PRIMARY KEY,
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);
CREATE TABLE IF NOT EXISTS asset_tags (
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY(asset_id, tag_id)
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    asset_id INTEGER REFERENCES assets(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    progress REAL NOT NULL DEFAULT 0,
    detail TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, id);

CREATE TABLE IF NOT EXISTS exports (
    id INTEGER PRIMARY KEY,
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    mode TEXT NOT NULL,
    output_path TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS screenshots (
    id INTEGER PRIMARY KEY,
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    timestamp_ms INTEGER NOT NULL,
    output_path TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_screenshots_asset ON screenshots(asset_id, id);

CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(
    entity_type UNINDEXED,
    entity_id UNINDEXED,
    asset_id UNINDEXED,
    text,
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT OR IGNORE INTO app_state(key, value) VALUES ('queue_paused', '0');
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    settings.ensure_directories()
    conn = sqlite3.connect(path or settings.database_path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def initialize_database(path: Path | None = None) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        vehicle_columns = {row[1] for row in conn.execute('PRAGMA table_info(vehicle_crops)')}
        additions = {
            'track_id': 'TEXT', 'scene_number': 'INTEGER', 'start_ms': 'INTEGER',
            'frame_path': 'TEXT', 'crop_bbox_json': 'TEXT', 'quality_json': 'TEXT',
            'quality_score': 'REAL', 'group_id': 'TEXT', 'similarity_json': 'TEXT',
            'archived': 'INTEGER NOT NULL DEFAULT 0',
            'pipeline_version': 'INTEGER NOT NULL DEFAULT 1',
        }
        for column, definition in additions.items():
            if column not in vehicle_columns:
                conn.execute(f'ALTER TABLE vehicle_crops ADD COLUMN {column} {definition}')
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_vehicle_track ON vehicle_crops(track_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_vehicle_group ON vehicle_crops(group_id)')
        conn.execute("UPDATE jobs SET status='queued', detail='Restored after restart' WHERE status='running'")
        conn.commit()


@contextmanager
def database(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
