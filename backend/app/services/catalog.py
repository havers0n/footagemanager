from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from ..db import database


VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac"}
ALL_EXTENSIONS = VIDEO_EXTENSIONS | IMAGE_EXTENSIONS | AUDIO_EXTENSIONS


def media_type_for(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    return None


def quick_hash(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.blake2b(digest_size=16)
    size = path.stat().st_size
    with path.open("rb") as handle:
        digest.update(handle.read(chunk_size))
        if size > chunk_size:
            handle.seek(max(0, size - chunk_size))
            digest.update(handle.read(chunk_size))
    digest.update(str(size).encode())
    return digest.hexdigest()


@dataclass
class ScanSummary:
    found: int = 0
    added: int = 0
    changed: int = 0
    missing: int = 0


def add_source(path_value: str, name: str | None = None) -> int:
    path = Path(path_value).expanduser().resolve()
    if not path.is_dir():
        raise ValueError("Folder does not exist or is not accessible")
    with database() as conn:
        cursor = conn.execute(
            "INSERT INTO sources(path, name) VALUES (?, ?) ON CONFLICT(path) DO UPDATE SET enabled=1, name=excluded.name RETURNING id",
            (str(path), name or path.name or str(path)),
        )
        return int(cursor.fetchone()[0])


def enqueue_job(conn, asset_id: int, kind: str = "index") -> None:
    existing = conn.execute(
        "SELECT 1 FROM jobs WHERE asset_id=? AND kind=? AND status IN ('queued','running')", (asset_id, kind)
    ).fetchone()
    if not existing:
        conn.execute("INSERT INTO jobs(asset_id, kind, detail) VALUES (?, ?, ?)", (asset_id, kind, "Waiting"))


def scan_source(source_id: int) -> ScanSummary:
    summary = ScanSummary()
    with database() as conn:
        source = conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        if not source:
            raise ValueError("Source not found")
        root = Path(source["path"])
        if not root.is_dir():
            conn.execute("UPDATE sources SET last_error=? WHERE id=?", ("Folder is unavailable", source_id))
            conn.execute("UPDATE assets SET status='offline' WHERE source_id=?", (source_id,))
            return summary

        known = {row["path"]: row for row in conn.execute("SELECT * FROM assets WHERE source_id=?", (source_id,))}
        seen: set[str] = set()
        try:
            for current_root, dirs, files in os.walk(root):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for filename in files:
                    path = Path(current_root) / filename
                    media_type = media_type_for(path)
                    if media_type is None:
                        continue
                    try:
                        stat = path.stat()
                    except OSError:
                        continue
                    path_text = str(path.resolve())
                    seen.add(path_text)
                    summary.found += 1
                    previous = known.get(path_text)
                    unchanged = previous and previous["size_bytes"] == stat.st_size and previous["mtime_ns"] == stat.st_mtime_ns
                    if unchanged:
                        if previous["status"] == "offline":
                            conn.execute("UPDATE assets SET status='ready', error=NULL WHERE id=?", (previous["id"],))
                        continue
                    fingerprint = quick_hash(path)
                    if previous:
                        conn.execute(
                            """UPDATE assets SET size_bytes=?, mtime_ns=?, quick_hash=?, status='pending', error=NULL,
                               updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                            (stat.st_size, stat.st_mtime_ns, fingerprint, previous["id"]),
                        )
                        conn.execute("DELETE FROM scenes WHERE asset_id=?", (previous["id"],))
                        conn.execute("DELETE FROM transcripts WHERE asset_id=?", (previous["id"],))
                        conn.execute("DELETE FROM audio_segments WHERE asset_id=?", (previous["id"],))
                        conn.execute("DELETE FROM vehicle_crops WHERE asset_id=?", (previous["id"],))
                        conn.execute("DELETE FROM jobs WHERE asset_id=? AND kind IN ('vehicles','vehicles_v2') AND status!='running'", (previous["id"],))
                        conn.execute("DELETE FROM search_fts WHERE asset_id=?", (previous["id"],))
                        enqueue_job(conn, previous["id"])
                        summary.changed += 1
                    else:
                        cursor = conn.execute(
                            """INSERT INTO assets(source_id,path,relative_path,filename,media_type,size_bytes,mtime_ns,quick_hash)
                               VALUES (?,?,?,?,?,?,?,?)""",
                            (source_id, path_text, str(path.relative_to(root)), filename, media_type, stat.st_size, stat.st_mtime_ns, fingerprint),
                        )
                        asset_id = int(cursor.lastrowid)
                        conn.execute(
                            "INSERT INTO search_fts(entity_type,entity_id,asset_id,text) VALUES ('asset',?,?,?)",
                            (asset_id, asset_id, f"{filename} {path.stem.replace('_', ' ')}"),
                        )
                        enqueue_job(conn, asset_id)
                        summary.added += 1
            missing_paths = set(known) - seen
            if missing_paths:
                placeholders = ",".join("?" for _ in missing_paths)
                conn.execute(f"UPDATE assets SET status='offline' WHERE path IN ({placeholders})", tuple(missing_paths))
                summary.missing = len(missing_paths)
            conn.execute("UPDATE sources SET last_scan_at=CURRENT_TIMESTAMP, last_error=NULL WHERE id=?", (source_id,))
        except Exception as exc:
            conn.execute("UPDATE sources SET last_error=? WHERE id=?", (str(exc), source_id))
            raise
    return summary


def scan_all_sources() -> list[ScanSummary]:
    with database() as conn:
        ids = [row[0] for row in conn.execute("SELECT id FROM sources WHERE enabled=1")]
    return [scan_source(source_id) for source_id in ids]
