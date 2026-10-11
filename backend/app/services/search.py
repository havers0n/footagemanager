from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

import numpy as np

from ..db import database
from .semantic import semantic_index


TOKEN = re.compile(r"[\w-]+", re.UNICODE)
VISUAL_CLIP_MS = 5_000
SPEECH_CLIP_MS = 8_000
AUDIO_INTENT_TERMS = {
    "audio", "sound", "noise", "music", "song", "voice", "speech",
    "siren", "gunshot", "shot", "explosion", "scream", "shout",
    "applause", "engine", "horn", "thunder", "rain", "footsteps",
    "звук", "аудио", "шум", "музыка", "песня", "голос", "речь",
    "сирена", "выстрел", "взрыв", "крик", "аплодисменты", "мотор",
    "двигатель", "гудок", "гром", "дождь", "шаги",
}
VISUAL_DISTANCE_MARGIN = 0.12
VISUAL_QUERY_EXPANSIONS = {
    "police car": ("police vehicle", "patrol car", "police cruiser", "law enforcement vehicle"),
    "police cars": ("police vehicles", "patrol cars", "police cruisers"),
    "полицейская машина": ("police car", "police vehicle", "patrol car", "police cruiser"),
    "полицейские машины": ("police cars", "police vehicles", "patrol cars"),
}
CHANNEL_WEIGHTS = {
    "Wanted-level HUD": 2.5,
    "Game interface": 2.0,
    "Speech": 0.45,
    "Filename or tag": 0.8,
    "Visual meaning": 1.0,
    "Audio meaning": 0.8,
    "Recently indexed": 1.0,
}
HUD_TERMS = {
    "wanted", "wanted level", "wanted stars", "police hud", "hud stars",
    "розыск", "уровень розыска", "звезда розыска", "звезды розыска",
    "звёзды розыска", "звезда", "звезды", "звёзды", "звезд", "звёзд",
    "star", "stars",
}
STAR_COUNT_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "одна": 1, "одну": 1, "две": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6,
}
UI_TOKENS = {
    "ui", "hud", "interface", "overlay", "minimap", "crosshair", "reticle",
    "menu", "interface", "интерфейс", "худ", "миникарта", "мини-карта", "меню",
    "прицел", "индикатор", "индикаторы", "иконка", "иконки", "значок", "значки",
    "подсказка", "подсказки",
}
UI_PHRASES = {
    "user interface", "game interface", "game hud", "video game hud", "mini map",
    "health bar", "ammo counter", "weapon wheel", "mission prompt", "button prompt",
    "игровой интерфейс", "мини карта", "полоса здоровья", "счётчик патронов",
    "счетчик патронов", "колесо оружия", "экранное меню", "значки интерфейса",
    "подсказки кнопок", "кнопки управления", "шкала здоровья", "полоска здоровья",
    "интерфейс гонки", "карта в углу",
}


def _fts_query(value: str) -> str:
    tokens = TOKEN.findall(value)
    return " AND ".join(f'"{token}"' for token in tokens)


def _visual_queries(value: str) -> list[str]:
    normalized = " ".join(TOKEN.findall(value.casefold()))
    queries = [value.strip()]
    queries.extend(VISUAL_QUERY_EXPANSIONS.get(normalized, ()))
    return list(dict.fromkeys(query for query in queries if query))


def _rrf(rankings: list[list[tuple[str, dict[str, Any]]]], k: int = 60) -> list[dict[str, Any]]:
    scores: dict[str, float] = defaultdict(float)
    payloads: dict[str, dict[str, Any]] = {}
    reasons: dict[str, set[str]] = defaultdict(set)
    for ranking in rankings:
        for rank, (key, payload) in enumerate(ranking, 1):
            weight = CHANNEL_WEIGHTS.get(payload.get("reason", ""), 1.0)
            scores[key] += weight / (k + rank)
            if key not in payloads:
                payloads[key] = payload
            elif not payloads[key].get("transcript") and payload.get("transcript"):
                payloads[key] = {**payloads[key], "transcript": payload["transcript"]}
            reasons[key].add(payload.get("reason", "Match"))
    ordered = sorted(scores, key=scores.get, reverse=True)
    return [{**payloads[key], "fusion_score": scores[key], "reason": " · ".join(sorted(reasons[key]))} for key in ordered]


def _bounded_window(start_ms: int, end_ms: int, limit_ms: int) -> tuple[int, int]:
    duration = max(0, end_ms - start_ms)
    if duration <= limit_ms:
        return start_ms, end_ms
    midpoint = start_ms + duration // 2
    clipped_start = max(start_ms, midpoint - limit_ms // 2)
    return clipped_start, min(end_ms, clipped_start + limit_ms)


def _window_at(timestamp_ms: int, start_ms: int, end_ms: int, limit_ms: int) -> tuple[int, int]:
    if end_ms - start_ms <= limit_ms:
        return start_ms, end_ms
    clipped_start = max(start_ms, timestamp_ms - limit_ms // 2)
    clipped_start = min(clipped_start, end_ms - limit_ms)
    return clipped_start, clipped_start + limit_ms


def _audio_intent(query: str) -> bool:
    return bool(set(TOKEN.findall(query.casefold())) & AUDIO_INTENT_TERMS)


def _hud_intent(query: str) -> bool:
    normalized = " ".join(TOKEN.findall(query.casefold()))
    return any(term in normalized for term in HUD_TERMS)


def _ui_intent(query: str) -> bool:
    normalized = " ".join(TOKEN.findall(query.casefold()))
    tokens = set(normalized.split())
    return bool(tokens & UI_TOKENS) or any(phrase in normalized for phrase in UI_PHRASES)


def _ui_queries(query: str) -> list[str]:
    normalized = " ".join(TOKEN.findall(query.casefold()))
    queries = [query.strip(), "video game HUD overlay with visible interface icons"]
    if any(term in normalized for term in ("minimap", "миникарта", "мини карта", "mini map")):
        queries.extend(("video game minimap in a screen corner", "gameplay with a visible map HUD"))
    elif any(term in normalized for term in ("menu", "меню", "prompt", "подсказ")):
        queries.extend(("video game menu overlay", "gameplay with interface prompts"))
    else:
        queries.extend((
            "gameplay with a visible user interface",
            "video game screen with HUD elements",
            "video game minimap in a screen corner",
            "video game controller button prompts on screen",
            "video game health ammo and status indicators",
            "gameplay with mission text and interface icons",
        ))
    return list(dict.fromkeys(item for item in queries if item))


def _wanted_star_count(query: str) -> int | None:
    tokens = TOKEN.findall(query.casefold())
    for token in tokens:
        if token.isdigit() and 1 <= int(token) <= 6:
            return int(token)
        if token in STAR_COUNT_WORDS:
            return STAR_COUNT_WORDS[token]
    return None


def _rank_visual_rows(query: str, limit: int = 100) -> list[dict[str, Any]]:
    candidates: dict[tuple[int, int], dict[str, Any]] = {}
    for variant in _visual_queries(query):
        vector = semantic_index.visual_text_vector(variant)
        for table_name in ("visual_frames", "visual"):
            rows = semantic_index.search(table_name, vector, limit)
            seen_scenes: set[tuple[int, int]] = set()
            for rank, row in enumerate(rows, 1):
                key = (int(row["asset_id"]), int(row["scene_id"]))
                if key in seen_scenes:
                    continue
                seen_scenes.add(key)
                distance = float(row.get("_distance", 1.0))
                candidate = candidates.setdefault(key, {**row, "_fusion": 0.0, "_distance": distance})
                candidate["_fusion"] += 1 / (20 + rank)
                if distance < float(candidate["_distance"]):
                    candidate.update(row)
                    candidate["_distance"] = distance
    if not candidates:
        return []
    best_distance = min(float(row["_distance"]) for row in candidates.values())
    kept = [
        row for row in candidates.values()
        if float(row["_distance"]) <= best_distance + VISUAL_DISTANCE_MARGIN
    ]
    return sorted(kept, key=lambda row: (-float(row["_fusion"]), float(row["_distance"])))[:limit]


def _rank_ui_rows(query: str, limit: int = 100) -> list[dict[str, Any]]:
    candidates: dict[tuple[int, int], dict[str, Any]] = {}
    variants = _ui_queries(query)
    negative_prompts = (
        "cinematic video frame without any interface or HUD",
        "movie title card or video game logo splash screen without gameplay",
        "a computer screen or television inside a room",
    )
    all_vectors = semantic_index.visual_text_vectors([*variants, *negative_prompts])
    positive_vectors = [np.asarray(vector, dtype="float32") for vector in all_vectors[:len(variants)]]
    negative_vectors = [np.asarray(vector, dtype="float32") for vector in all_vectors[len(variants):]]
    for variant, vector in zip(variants, positive_vectors, strict=True):
        for rank, row in enumerate(semantic_index.search("visual_ui", vector, limit), 1):
            key = (int(row["asset_id"]), int(row["scene_id"]))
            distance = float(row.get("_distance", 1.0))
            candidate = candidates.setdefault(key, {**row, "_fusion": 0.0, "_distance": distance})
            candidate["_fusion"] += 1 / (20 + rank)
            if distance < float(candidate["_distance"]):
                candidate.update(row)
                candidate["_distance"] = distance
    if not candidates:
        return []
    for candidate in candidates.values():
        image_vector = np.asarray(candidate["vector"], dtype="float32")
        positive_score = max(float(image_vector @ vector) for vector in positive_vectors)
        negative_score = max(float(image_vector @ vector) for vector in negative_vectors)
        candidate["_ui_margin"] = positive_score - negative_score
    best_distance = min(float(row["_distance"]) for row in candidates.values())
    kept = [
        row for row in candidates.values()
        if float(row["_distance"]) <= best_distance + 0.09 and float(row["_ui_margin"]) >= 0.01
    ]
    return sorted(
        kept,
        key=lambda row: (-float(row["_ui_margin"]), -float(row["_fusion"]), float(row["_distance"])),
    )[:limit]


def _base_result(
    asset: Any,
    scene: Any | None,
    transcript: str | None = None,
    bounds: tuple[int, int] | None = None,
    frame_id: int | None = None,
) -> dict[str, Any]:
    start_ms, end_ms = bounds or (
        (scene["start_ms"], scene["end_ms"])
        if scene else (0, asset["duration_ms"] or 0)
    )
    thumbnail = scene["thumbnail_path"] if scene and scene["thumbnail_path"] else asset["thumbnail_path"]
    return {
        "result_id": f"{asset['id']}:{scene['id'] if scene else 0}",
        "asset_id": asset["id"],
        "scene_id": scene["id"] if scene else None,
        "media_type": asset["media_type"],
        "filename": asset["filename"],
        "source_name": asset["source_name"],
        "path": asset["path"],
        "start_ms": start_ms,
        "end_ms": end_ms,
        "duration_ms": asset["duration_ms"],
        "width": asset["width"],
        "height": asset["height"],
        "fps": asset["fps"],
        "thumbnail_url": (
            f"/api/frames/{frame_id}/thumbnail"
            if frame_id is not None else
            f"/api/scenes/{scene['id']}/thumbnail"
            if scene and scene["thumbnail_path"] else
            (f"/api/assets/{asset['id']}/thumbnail" if thumbnail else None)
        ),
        "preview_url": f"/api/assets/{asset['id']}/media",
        "transcript": transcript,
    }


def search_assets(query: str, media_types: list[str], source_id: int | None, min_duration: int | None, max_duration: int | None, orientation: str | None, has_speech: bool | None, limit: int = 60) -> dict[str, Any]:
    filters = ["a.status='ready'"]
    params: list[Any] = []
    if media_types:
        filters.append(f"a.media_type IN ({','.join('?' for _ in media_types)})")
        params.extend(media_types)
    if source_id is not None:
        filters.append("a.source_id=?")
        params.append(source_id)
    if orientation == "vertical":
        filters.append("a.height>a.width")
    elif orientation == "horizontal":
        filters.append("a.width>=a.height")
    if has_speech is not None:
        filters.append("a.has_speech=?")
        params.append(int(has_speech))
    where = " AND ".join(filters)

    with database() as conn:
        asset_rows = conn.execute(f"SELECT a.*,s.name source_name FROM assets a JOIN sources s ON s.id=a.source_id WHERE {where}", params).fetchall()
        assets = {row["id"]: row for row in asset_rows}
        scenes_by_asset: dict[int, list[Any]] = defaultdict(list)
        if assets:
            placeholders = ",".join("?" for _ in assets)
            for row in conn.execute(f"SELECT * FROM scenes WHERE asset_id IN ({placeholders})", tuple(assets)):
                scenes_by_asset[row["asset_id"]].append(row)
        rankings: list[list[tuple[str, dict[str, Any]]]] = []
        if query.strip() and assets:
            fts = _fts_query(query)
            keyword_ranking: list[tuple[str, dict[str, Any]]] = []
            if fts:
                rows = conn.execute(
                    f"""SELECT f.asset_id,f.entity_type,f.entity_id,f.text,bm25(search_fts) rank
                    FROM search_fts f WHERE search_fts MATCH ? AND f.asset_id IN ({placeholders}) ORDER BY rank LIMIT 100""",
                    (fts, *assets.keys()),
                ).fetchall()
                for row in rows:
                    asset = assets.get(row["asset_id"])
                    if not asset:
                        continue
                    transcript = row["text"] if row["entity_type"] == "transcript" else None
                    scene = None
                    if transcript:
                        transcript_row = conn.execute("SELECT * FROM transcripts WHERE id=?", (row["entity_id"],)).fetchone()
                        if transcript_row:
                            scene = next((item for item in scenes_by_asset[asset["id"]] if item["start_ms"] <= transcript_row["start_ms"] < item["end_ms"]), None)
                            bounds = _bounded_window(transcript_row["start_ms"], transcript_row["end_ms"], SPEECH_CLIP_MS)
                        else:
                            bounds = None
                    else:
                        bounds = None
                    scene = scene or (scenes_by_asset[asset["id"]][0] if scenes_by_asset[asset["id"]] else None)
                    payload = _base_result(asset, scene, transcript, bounds)
                    payload["reason"] = "Speech" if transcript else "Filename or tag"
                    keyword_ranking.append((payload["result_id"], payload))
            rankings.append(keyword_ranking)
        elif not query.strip():
            recent = sorted(asset_rows, key=lambda row: row["updated_at"], reverse=True)
            ranking = []
            for asset in recent:
                scene = scenes_by_asset[asset["id"]][0] if scenes_by_asset[asset["id"]] else None
                bounds = _bounded_window(scene["start_ms"], scene["end_ms"], VISUAL_CLIP_MS) if scene else None
                payload = _base_result(asset, scene, bounds=bounds)
                payload["reason"] = "Recently indexed"
                ranking.append((payload["result_id"], payload))
            rankings.append(ranking)

        if query.strip() and assets and _hud_intent(query):
            wanted_count = _wanted_star_count(query)
            hud_params: list[Any] = list(assets.keys())
            hud_filter = f"h.asset_id IN ({placeholders})"
            if wanted_count is not None:
                hud_filter += " AND h.star_count=?"
                hud_params.append(wanted_count)
            hud_rows = conn.execute(
                f"SELECT h.* FROM hud_detections h WHERE {hud_filter} ORDER BY h.confidence DESC,h.start_ms LIMIT 100",
                hud_params,
            ).fetchall()
            hud_ranking: list[tuple[str, dict[str, Any]]] = []
            for row in hud_rows:
                asset = assets.get(row["asset_id"])
                if not asset:
                    continue
                scene = next(
                    (item for item in scenes_by_asset[asset["id"]] if item["id"] == row["scene_id"]),
                    None,
                )
                if not scene:
                    continue
                timestamp_ms = (int(row["start_ms"]) + int(row["end_ms"])) // 2
                bounds = _window_at(timestamp_ms, scene["start_ms"], scene["end_ms"], VISUAL_CLIP_MS)
                payload = _base_result(asset, scene, bounds=bounds)
                payload["result_id"] = f"{asset['id']}:hud:{row['id']}"
                payload["thumbnail_url"] = f"/api/hud/{row['id']}/thumbnail"
                payload["reason"] = "Wanted-level HUD"
                payload["transcript"] = f"Розыск: {row['star_count']} ★"
                hud_ranking.append((payload["result_id"], payload))
            rankings.append(hud_ranking)

        if query.strip() and assets and _ui_intent(query) and semantic_index.available:
            try:
                ui_rows = _rank_ui_rows(query, 100)
                ui_ranking = []
                for row in ui_rows:
                    asset = assets.get(int(row["asset_id"]))
                    if not asset:
                        continue
                    scene = next(
                        (item for item in scenes_by_asset[asset["id"]] if item["id"] == int(row["scene_id"])),
                        None,
                    )
                    if not scene:
                        continue
                    raw_frame_id = int(row.get("frame_id", -1))
                    frame_id = raw_frame_id if raw_frame_id >= 0 else None
                    timestamp_ms = int(row["start_ms"])
                    bounds = _window_at(timestamp_ms, scene["start_ms"], scene["end_ms"], VISUAL_CLIP_MS)
                    payload = _base_result(asset, scene, bounds=bounds, frame_id=frame_id)
                    payload["result_id"] = f"{asset['id']}:ui:{scene['id']}"
                    payload["reason"] = "Game interface"
                    payload["transcript"] = "Игровой интерфейс / HUD"
                    ui_ranking.append((payload["result_id"], payload))
                rankings.append(ui_ranking)
            except Exception:
                pass

        if query.strip() and assets and not _hud_intent(query) and not _ui_intent(query) and semantic_index.available:
            try:
                visual_rows = _rank_visual_rows(query, 100)
                visual_ranking = []
                for row in visual_rows:
                    asset = assets.get(int(row["asset_id"]))
                    if not asset:
                        continue
                    scene = next((item for item in scenes_by_asset[asset["id"]] if item["id"] == int(row["scene_id"])), None)
                    if not scene:
                        continue
                    frame_id = int(row["frame_id"]) if row.get("frame_id") is not None else None
                    timestamp_ms = (
                        int(row["start_ms"])
                        if frame_id is not None else
                        (scene["start_ms"] + scene["end_ms"]) // 2
                    )
                    bounds = _window_at(timestamp_ms, scene["start_ms"], scene["end_ms"], VISUAL_CLIP_MS)
                    payload = _base_result(asset, scene, bounds=bounds, frame_id=frame_id)
                    payload["reason"] = "Visual meaning"
                    visual_ranking.append((payload["result_id"], payload))
                rankings.append(visual_ranking)
            except Exception:
                pass
            search_video_audio = _audio_intent(query)
            searchable_audio_assets = {
                asset_id for asset_id, asset in assets.items()
                if asset["media_type"] == "audio" or (search_video_audio and asset["media_type"] == "video")
            }
            try:
                if not searchable_audio_assets:
                    raise LookupError
                audio_rows = semantic_index.search("audio", semantic_index.audio_text_vector(query), 100)
                audio_ranking = []
                for row in audio_rows:
                    asset = assets.get(int(row["asset_id"]))
                    if not asset or asset["id"] not in searchable_audio_assets:
                        continue
                    start_ms = int(row["start_ms"])
                    scene = next((item for item in scenes_by_asset[asset["id"]] if item["start_ms"] <= start_ms < item["end_ms"]), None)
                    bounds = (start_ms, int(row["end_ms"]))
                    payload = _base_result(asset, scene, bounds=bounds)
                    if scene is None:
                        payload["result_id"] = f"{asset['id']}:audio:{start_ms}"
                    payload["reason"] = "Audio meaning"
                    audio_ranking.append((payload["result_id"], payload))
                rankings.append(audio_ranking)
            except Exception:
                pass

        merged = _rrf(rankings)
        if min_duration is not None:
            merged = [item for item in merged if item["end_ms"] - item["start_ms"] >= min_duration]
        if max_duration is not None:
            merged = [item for item in merged if item["end_ms"] - item["start_ms"] <= max_duration]
        merged = merged[:limit]
        for result in merged:
            tag_rows = conn.execute("SELECT t.name FROM tags t JOIN asset_tags at ON at.tag_id=t.id WHERE at.asset_id=? ORDER BY t.name", (result["asset_id"],)).fetchall()
            result["tags"] = [row[0] for row in tag_rows]
            result["score"] = round(result.pop("fusion_score", 0.0), 6)
        return {"query": query, "total": len(merged), "results": merged}
