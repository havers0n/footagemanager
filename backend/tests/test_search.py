from __future__ import annotations

from app.db import database
from app.services.search import _fts_query, _hud_intent, _ui_intent, _visual_queries, _wanted_star_count, search_assets
from app.services.semantic import semantic_index


def _ready_video() -> tuple[int, int]:
    with database() as conn:
        source_id = conn.execute(
            "INSERT INTO sources(path,name) VALUES (?,?)", ("/library", "Library")
        ).lastrowid
        asset_id = conn.execute(
            """INSERT INTO assets(
                source_id,path,relative_path,filename,media_type,size_bytes,mtime_ns,
                quick_hash,duration_ms,width,height,status
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,'ready')""",
            (source_id, "/library/test.mp4", "test.mp4", "test.mp4", "video", 1, 1, "hash", 60_000, 1920, 1080),
        ).lastrowid
        scene_id = conn.execute(
            "INSERT INTO scenes(asset_id,scene_number,start_ms,end_ms,thumbnail_path) VALUES (?,?,?,?,?)",
            (asset_id, 1, 10_000, 40_000, "/tmp/scene.webp"),
        ).lastrowid
    return int(asset_id), int(scene_id)


def test_multiword_text_search_requires_every_word():
    assert _fts_query("police car") == '"police" AND "car"'


def test_police_car_query_expands_to_visual_synonyms():
    queries = _visual_queries("police car")
    assert queries[0] == "police car"
    assert "patrol car" in queries
    assert "police cruiser" in queries


def test_wanted_hud_query_understands_russian_and_star_count():
    assert _hud_intent("четыре звезды розыска")
    assert _wanted_star_count("четыре звезды розыска") == 4
    assert _wanted_star_count("wanted level 5 stars") == 5


def test_general_ui_intent_does_not_confuse_normal_words():
    assert _ui_intent("игровой интерфейс")
    assert _ui_intent("minimap")
    assert _ui_intent("HUD icons")
    assert not _ui_intent("building at night")


def test_wanted_hud_search_returns_exact_detection(monkeypatch):
    asset_id, scene_id = _ready_video()
    with database() as conn:
        detection_id = conn.execute(
            """INSERT INTO hud_detections(
                asset_id,scene_id,start_ms,end_ms,star_count,confidence,thumbnail_path
            ) VALUES (?,?,?,?,?,?,?)""",
            (asset_id, scene_id, 20_000, 24_000, 4, 0.98, "/tmp/wanted.webp"),
        ).lastrowid
    monkeypatch.setattr(type(semantic_index), "available", property(lambda self: False))

    result = search_assets("4 звезды розыска", ["video"], None, None, None, None, None, 10)

    assert len(result["results"]) == 1
    item = result["results"][0]
    assert item["result_id"] == f"{asset_id}:hud:{detection_id}"
    assert item["thumbnail_url"] == f"/api/hud/{detection_id}/thumbnail"
    assert item["transcript"] == "Wanted level: 4 ★"
    assert (item["start_ms"], item["end_ms"]) == (19_500, 24_500)


def test_ui_search_uses_enlarged_corner_index(monkeypatch):
    asset_id, scene_id = _ready_video()
    calls: list[str] = []
    monkeypatch.setattr(type(semantic_index), "available", property(lambda self: True))
    monkeypatch.setattr(
        semantic_index,
        "visual_text_vectors",
        lambda queries: [
            [-1.0] if any(word in query for word in ("without", "title card", "computer screen")) else [1.0]
            for query in queries
        ],
    )

    def fake_search(table, vector, limit):
        calls.append(table)
        if table == "visual_ui":
            return [{
                "asset_id": asset_id, "scene_id": scene_id, "frame_id": 77,
                "start_ms": 20_000, "_distance": 0.2, "vector": [1.0],
            }]
        return []

    monkeypatch.setattr(semantic_index, "search", fake_search)

    result = search_assets("игровой интерфейс", ["video"], None, None, None, None, None, 10)

    assert calls and set(calls) == {"visual_ui"}
    assert len(result["results"]) == 1
    item = result["results"][0]
    assert item["reason"] == "Game interface"
    assert item["transcript"] == "Game interface / HUD"
    assert item["thumbnail_url"] == "/api/frames/77/thumbnail"


def test_partial_transcript_does_not_match_multiword_query(monkeypatch):
    asset_id, scene_id = _ready_video()
    with database() as conn:
        transcript_id = conn.execute(
            "INSERT INTO transcripts(asset_id,start_ms,end_ms,text) VALUES (?,?,?,?)",
            (asset_id, 12_000, 13_000, "Got this guy's car"),
        ).lastrowid
        conn.execute(
            "INSERT INTO search_fts(entity_type,entity_id,asset_id,text) VALUES ('transcript',?,?,?)",
            (transcript_id, asset_id, "Got this guy's car"),
        )
    monkeypatch.setattr(type(semantic_index), "available", property(lambda self: False))

    result = search_assets("police car", ["video"], None, None, None, None, None, 10)

    assert result["results"] == []


def test_visual_result_is_five_seconds_and_duration_filter_uses_clip(monkeypatch):
    asset_id, scene_id = _ready_video()
    monkeypatch.setattr(type(semantic_index), "available", property(lambda self: True))
    monkeypatch.setattr(semantic_index, "visual_text_vector", lambda query: [1.0])
    monkeypatch.setattr(
        semantic_index,
        "search",
        lambda table, vector, limit: [{"asset_id": asset_id, "scene_id": scene_id, "start_ms": 10_000, "_distance": 0.2}] if table == "visual" else [],
    )

    result = search_assets("water", ["video"], None, None, 5_000, None, None, 10)

    assert len(result["results"]) == 1
    item = result["results"][0]
    assert (item["start_ms"], item["end_ms"]) == (22_500, 27_500)
    assert item["reason"] == "Visual meaning"
    assert item["thumbnail_url"] == f"/api/scenes/{scene_id}/thumbnail"


def test_generic_visual_query_does_not_search_video_audio(monkeypatch):
    asset_id, scene_id = _ready_video()
    calls: list[str] = []
    monkeypatch.setattr(type(semantic_index), "available", property(lambda self: True))
    monkeypatch.setattr(semantic_index, "visual_text_vector", lambda query: [1.0])
    monkeypatch.setattr(semantic_index, "audio_text_vector", lambda query: calls.append("audio") or [1.0])
    monkeypatch.setattr(
        semantic_index,
        "search",
        lambda table, vector, limit: calls.append(table) or ([{"asset_id": asset_id, "scene_id": scene_id, "start_ms": 10_000, "_distance": 0.2}] if table == "visual" else []),
    )

    search_assets("water", ["video"], None, None, None, None, None, 10)

    assert calls == ["visual_frames", "visual"]


def test_sound_query_keeps_exact_audio_window(monkeypatch):
    asset_id, scene_id = _ready_video()
    monkeypatch.setattr(type(semantic_index), "available", property(lambda self: True))
    monkeypatch.setattr(semantic_index, "visual_text_vector", lambda query: [1.0])
    monkeypatch.setattr(semantic_index, "audio_text_vector", lambda query: [1.0])

    def fake_search(table, vector, limit):
        if table == "visual":
            return []
        return [{"asset_id": asset_id, "start_ms": 15_000, "end_ms": 25_000}]

    monkeypatch.setattr(semantic_index, "search", fake_search)

    result = search_assets("siren sound", ["video"], None, None, None, None, None, 10)

    item = result["results"][0]
    assert item["scene_id"] == scene_id
    assert (item["start_ms"], item["end_ms"]) == (15_000, 25_000)
    assert item["reason"] == "Audio meaning"
