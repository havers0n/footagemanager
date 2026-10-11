from __future__ import annotations

from pathlib import Path

from app.db import database
from app.services.catalog import add_source, media_type_for, quick_hash, scan_source


def test_media_type_detection():
    assert media_type_for(Path("trailer.MP4")) == "video"
    assert media_type_for(Path("screen.webp")) == "image"
    assert media_type_for(Path("siren.FLAC")) == "audio"
    assert media_type_for(Path("notes.txt")) is None


def test_scan_add_change_and_missing(isolated_data_dir):
    library = isolated_data_dir / "library"
    library.mkdir()
    image = library / "Lucia_police.jpg"
    image.write_bytes(b"first")
    source_id = add_source(str(library))

    first = scan_source(source_id)
    assert first.added == 1
    with database() as conn:
        asset = conn.execute("SELECT * FROM assets").fetchone()
        assert asset["media_type"] == "image"
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1

    image.write_bytes(b"changed material")
    second = scan_source(source_id)
    assert second.changed == 1

    image.unlink()
    third = scan_source(source_id)
    assert third.missing == 1
    with database() as conn:
        assert conn.execute("SELECT status FROM assets").fetchone()[0] == "offline"


def test_quick_hash_changes_with_content(isolated_data_dir):
    path = isolated_data_dir / "clip.mp4"
    path.write_bytes(b"abc")
    before = quick_hash(path)
    path.write_bytes(b"xyz")
    assert quick_hash(path) != before

