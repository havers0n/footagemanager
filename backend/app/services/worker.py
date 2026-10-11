from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path

from ..config import settings
from ..db import database
from .catalog import scan_all_sources
from .media import (
    audio_waveform,
    audio_windows,
    create_preview,
    detect_scenes,
    hud_montage,
    image_thumbnail,
    merge_wanted_observations,
    probe,
    video_thumbnail,
    wanted_hud_observations,
)
from .semantic import semantic_index


class IndexWorker:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._scanner: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if self._stop.is_set():
            self._stop = threading.Event()
            self._wake = threading.Event()
        self._thread = threading.Thread(target=self._run, name="footage-index-worker", daemon=True)
        self._scanner = threading.Thread(target=self._scan_loop, name="footage-folder-scanner", daemon=True)
        self._thread.start()
        self._scanner.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        if self._scanner and self._scanner.is_alive():
            self._scanner.join(timeout=5)

    def wake(self) -> None:
        self._wake.set()

    def paused(self) -> bool:
        with database() as conn:
            row = conn.execute("SELECT value FROM app_state WHERE key='queue_paused'").fetchone()
            return bool(row and row[0] == "1")

    def set_paused(self, paused: bool) -> None:
        with database() as conn:
            conn.execute("UPDATE app_state SET value=? WHERE key='queue_paused'", ("1" if paused else "0",))
        if not paused:
            self.wake()

    def retry_failed(self) -> int:
        with database() as conn:
            cursor = conn.execute("UPDATE jobs SET status='queued', progress=0, detail='Retrying' WHERE status='failed'")
            count = cursor.rowcount
        self.wake()
        return count

    def enqueue_visual_reindex(self) -> int:
        with database() as conn:
            asset_ids = [row[0] for row in conn.execute(
                "SELECT id FROM assets WHERE status='ready' AND media_type='video' ORDER BY id"
            )]
            count = 0
            for asset_id in asset_ids:
                exists = conn.execute(
                    "SELECT 1 FROM jobs WHERE asset_id=? AND kind='visual_reindex' AND status IN ('queued','running')",
                    (asset_id,),
                ).fetchone()
                if not exists:
                    conn.execute(
                        "INSERT INTO jobs(asset_id,kind,status,progress,detail) VALUES (?,'visual_reindex','queued',0,'Waiting')",
                        (asset_id,),
                    )
                    count += 1
        self.wake()
        return count

    def enqueue_vehicles(self, asset_id: int | None = None, force: bool = False) -> int:
        with database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            assets = conn.execute(
                "SELECT id FROM assets WHERE media_type='video' AND status='ready' AND (? IS NULL OR id=?)",
                (asset_id, asset_id),
            ).fetchall()
            count = 0
            for asset in assets:
                if not conn.execute(
                    "SELECT 1 FROM jobs WHERE asset_id=? AND kind='vehicles_v2' AND (status IN ('queued','running') OR (status='completed' AND ?=0))",
                    (asset['id'], int(force)),
                ).fetchone():
                    conn.execute("INSERT INTO jobs(asset_id,kind,detail) VALUES (?,'vehicles_v2','Отслеживание автомобилей')", (asset['id'],))
                    count += 1
        self.wake()
        return count

    def _process_vehicles(self, job: dict) -> None:
        from .vehicles import extract_vehicles
        with database() as conn:
            asset = conn.execute('SELECT * FROM assets WHERE id=?', (job['asset_id'],)).fetchone()
        if not asset:
            raise RuntimeError('Видео удалено')

        def cancelled():
            while self.paused() and not self._stop.is_set():
                self._stop.wait(0.5)
            return self._stop.is_set()

        extract_vehicles(dict(asset), job['id'],
                         lambda value, detail: self._job_progress(job['id'], value, detail), cancelled)

    def enqueue_hud_reindex(self) -> int:
        with database() as conn:
            asset_ids = [row[0] for row in conn.execute(
                "SELECT id FROM assets WHERE status='ready' AND media_type='video' ORDER BY id"
            )]
            count = 0
            for asset_id in asset_ids:
                exists = conn.execute(
                    "SELECT 1 FROM jobs WHERE asset_id=? AND kind='hud_reindex' AND status IN ('queued','running')",
                    (asset_id,),
                ).fetchone()
                if not exists:
                    conn.execute(
                        "INSERT INTO jobs(asset_id,kind,status,progress,detail) VALUES (?,'hud_reindex','queued',0,'Waiting')",
                        (asset_id,),
                    )
                    count += 1
        self.wake()
        return count

    def enqueue_ui_reindex(self) -> int:
        with database() as conn:
            asset_ids = [row[0] for row in conn.execute(
                """SELECT DISTINCT a.id FROM assets a JOIN scene_frames f ON f.asset_id=a.id
                   WHERE a.status='ready' AND a.media_type='video' ORDER BY a.id"""
            )]
            count = 0
            for asset_id in asset_ids:
                exists = conn.execute(
                    "SELECT 1 FROM jobs WHERE asset_id=? AND kind='ui_reindex' AND status IN ('queued','running')",
                    (asset_id,),
                ).fetchone()
                if not exists:
                    conn.execute(
                        "INSERT INTO jobs(asset_id,kind,status,progress,detail) VALUES (?,'ui_reindex','queued',0,'Waiting')",
                        (asset_id,),
                    )
                    count += 1
        self.wake()
        return count

    def _scan_loop(self) -> None:
        while not self._stop.is_set():
            try:
                scan_all_sources()
                self.wake()
            except Exception:
                pass
            self._stop.wait(settings.scan_interval_seconds)

    def _next_job(self):
        with database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
            if row:
                conn.execute(
                    "UPDATE jobs SET status='running', attempts=attempts+1, started_at=CURRENT_TIMESTAMP, detail='Reading media' WHERE id=?",
                    (row["id"],),
                )
                return dict(row)
        return None

    def _run(self) -> None:
        while not self._stop.is_set():
            if self.paused():
                self._wake.wait(2)
                self._wake.clear()
                continue
            job = self._next_job()
            if not job:
                self._wake.wait(2)
                self._wake.clear()
                continue
            try:
                if job["kind"] in ('vehicles', 'vehicles_v2'):
                    self._process_vehicles(job)
                elif job["kind"] == "visual_reindex":
                    self._process_visual_reindex(job)
                elif job["kind"] == "hud_reindex":
                    self._process_hud_reindex(job)
                elif job["kind"] == "ui_reindex":
                    self._process_ui_reindex(job)
                else:
                    self._process(job)
                with database() as conn:
                    conn.execute(
                        "UPDATE jobs SET status='completed', progress=1, detail='Ready', finished_at=CURRENT_TIMESTAMP WHERE id=?",
                        (job["id"],),
                    )
            except Exception as exc:
                with database() as conn:
                    conn.execute(
                        "UPDATE jobs SET status='failed', detail=?, finished_at=CURRENT_TIMESTAMP WHERE id=?",
                        (str(exc)[-4000:], job["id"]),
                    )
                    if job['kind'] not in ('vehicles', 'vehicles_v2'):
                        conn.execute("UPDATE assets SET status='error', error=? WHERE id=?", (str(exc)[-4000:], job["asset_id"]))

    def _job_progress(self, job_id: int, progress: float, detail: str) -> None:
        with database() as conn:
            conn.execute("UPDATE jobs SET progress=?, detail=? WHERE id=?", (progress, detail, job_id))

    @staticmethod
    def _frame_timestamps(start_ms: int, end_ms: int, interval_ms: int = 2_000) -> list[int]:
        duration = max(0, end_ms - start_ms)
        if duration <= interval_ms:
            return [start_ms + duration // 2]
        timestamps = list(range(start_ms + interval_ms // 2, end_ms, interval_ms))
        return timestamps or [start_ms + duration // 2]

    def _process_visual_reindex(self, job: dict) -> None:
        self._job_progress(job["id"], 0.01, "Loading visual model")
        with database() as conn:
            asset = conn.execute("SELECT * FROM assets WHERE id=?", (job["asset_id"],)).fetchone()
            scenes = conn.execute("SELECT * FROM scenes WHERE asset_id=? ORDER BY scene_number", (job["asset_id"],)).fetchall()
        if not asset or not scenes:
            raise RuntimeError("No indexed scenes available")
        source = Path(asset["path"])
        if not source.is_file():
            raise FileNotFoundError("Source file is unavailable")
        if not semantic_index.available:
            raise RuntimeError("Semantic search is unavailable")

        with database() as conn:
            conn.execute("DELETE FROM scene_frames WHERE asset_id=?", (asset["id"],))
        semantic_index.clear_visual_frames(asset["id"])

        cache = settings.cache_dir / str(asset["id"]) / "frames"
        cache.mkdir(parents=True, exist_ok=True)
        work_items = [
            (scene, timestamp_ms)
            for scene in scenes
            for timestamp_ms in self._frame_timestamps(scene["start_ms"], scene["end_ms"])
        ]
        total = len(work_items)
        frame_numbers: dict[int, int] = {}
        for completed, (scene, timestamp_ms) in enumerate(work_items, 1):
            frame_index = frame_numbers.get(scene["id"], 0) + 1
            frame_numbers[scene["id"]] = frame_index
            frame_path = cache / f"scene-{scene['scene_number']:05d}-{frame_index:03d}.webp"
            video_thumbnail(source, frame_path, timestamp_ms)
            with database() as conn:
                cursor = conn.execute(
                    "INSERT INTO scene_frames(scene_id,asset_id,timestamp_ms,thumbnail_path) VALUES (?,?,?,?)",
                    (scene["id"], asset["id"], timestamp_ms, str(frame_path)),
                )
                frame_id = int(cursor.lastrowid)
            semantic_index.add_visual_frame(asset["id"], scene["id"], frame_id, timestamp_ms, frame_path)
            progress = 0.05 + 0.9 * completed / total
            self._job_progress(job["id"], progress, f"Visual frames {completed}/{total}")
        with database() as conn:
            exists = conn.execute(
                "SELECT 1 FROM jobs WHERE asset_id=? AND kind='ui_reindex' AND status IN ('queued','running')",
                (asset["id"],),
            ).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO jobs(asset_id,kind,status,progress,detail) VALUES (?,'ui_reindex','queued',0,'Waiting')",
                    (asset["id"],),
                )

    def _index_wanted_hud(
        self,
        asset_id: int,
        source: Path,
        width: int,
        height: int,
        duration_ms: int,
        scenes: list,
    ) -> int:
        cache = settings.cache_dir / str(asset_id) / "hud"
        if cache.is_dir():
            shutil.rmtree(cache)
        cache.mkdir(parents=True, exist_ok=True)
        with database() as conn:
            conn.execute("DELETE FROM hud_detections WHERE asset_id=?", (asset_id,))

        observations = wanted_hud_observations(source, width, height, duration_ms)
        detections = merge_wanted_observations(observations, duration_ms)
        created = 0
        for number, (start_ms, end_ms, star_count, confidence) in enumerate(detections, 1):
            timestamp_ms = (start_ms + end_ms) // 2
            scene = next(
                (item for item in scenes if int(item[1]) <= timestamp_ms < int(item[2])),
                None,
            )
            if scene is None:
                continue
            with database() as conn:
                nearest_frame = conn.execute(
                    """SELECT thumbnail_path FROM scene_frames WHERE asset_id=?
                       ORDER BY ABS(timestamp_ms-?) LIMIT 1""",
                    (asset_id, timestamp_ms),
                ).fetchone()
            thumbnail = Path(nearest_frame["thumbnail_path"]) if nearest_frame else None
            if not thumbnail or not thumbnail.is_file():
                thumbnail = cache / f"wanted-{number:05d}-{star_count}-stars.webp"
                video_thumbnail(source, thumbnail, timestamp_ms)
            with database() as conn:
                conn.execute(
                    """INSERT INTO hud_detections(
                        asset_id,scene_id,start_ms,end_ms,star_count,confidence,thumbnail_path
                    ) VALUES (?,?,?,?,?,?,?)""",
                    (asset_id, int(scene[0]), start_ms, end_ms, star_count, confidence, str(thumbnail)),
                )
            created += 1
        return created

    def _process_hud_reindex(self, job: dict) -> None:
        self._job_progress(job["id"], 0.02, "Scanning wanted-level HUD")
        with database() as conn:
            asset = conn.execute("SELECT * FROM assets WHERE id=?", (job["asset_id"],)).fetchone()
            scenes = conn.execute(
                "SELECT id,start_ms,end_ms FROM scenes WHERE asset_id=? ORDER BY scene_number",
                (job["asset_id"],),
            ).fetchall()
        if not asset or asset["media_type"] != "video" or not scenes:
            raise RuntimeError("No indexed video scenes available")
        source = Path(asset["path"])
        if not source.is_file():
            raise FileNotFoundError("Source file is unavailable")
        count = self._index_wanted_hud(
            int(asset["id"]), source, int(asset["width"] or 0), int(asset["height"] or 0),
            int(asset["duration_ms"] or 0), list(scenes),
        )
        self._job_progress(job["id"], 0.98, f"Wanted HUD: {count} fragments")

    def _process_ui_reindex(self, job: dict) -> None:
        self._job_progress(job["id"], 0.02, "Preparing enlarged UI regions")
        with database() as conn:
            asset = conn.execute("SELECT * FROM assets WHERE id=?", (job["asset_id"],)).fetchone()
            frames = conn.execute(
                """SELECT f.id frame_id,f.scene_id,f.timestamp_ms,f.thumbnail_path
                   FROM scene_frames f WHERE f.asset_id=? ORDER BY f.timestamp_ms""",
                (job["asset_id"],),
            ).fetchall()
        if not asset or asset["media_type"] != "video" or not frames:
            raise RuntimeError("No visual frames available for UI indexing")
        if not semantic_index.available:
            raise RuntimeError("Semantic search is unavailable")
        cache = settings.cache_dir / str(asset["id"]) / "ui"
        if cache.is_dir():
            shutil.rmtree(cache)
        cache.mkdir(parents=True, exist_ok=True)
        records = []
        total = len(frames)
        for completed, frame in enumerate(frames, 1):
            source = Path(frame["thumbnail_path"])
            if not source.is_file():
                continue
            montage = cache / f"frame-{int(frame['frame_id']):06d}.webp"
            hud_montage(source, montage)
            records.append({
                "asset_id": int(asset["id"]),
                "scene_id": int(frame["scene_id"]),
                "frame_id": int(frame["frame_id"]),
                "start_ms": int(frame["timestamp_ms"]),
                "image_path": str(montage),
            })
            if completed % 50 == 0 or completed == total:
                self._job_progress(job["id"], 0.03 + 0.22 * completed / total, f"UI crops {completed}/{total}")
        self._job_progress(job["id"], 0.26, f"Learning UI appearance from {len(records)} frames")
        semantic_index.replace_visual_ui(int(asset["id"]), records)
        self._job_progress(job["id"], 0.98, f"UI frames indexed: {len(records)}")

    def _transcribe(self, asset_id: int, source: Path) -> None:
        if not settings.transcription_enabled:
            return
        try:
            import torch
            import whisperx
        except ImportError:
            return
        device = "cuda" if torch.cuda.is_available() else "cpu"
        compute_type = "float16" if device == "cuda" else "int8"
        model = whisperx.load_model(settings.whisper_model, device, compute_type=compute_type)
        audio = whisperx.load_audio(str(source))
        result = model.transcribe(audio, batch_size=8)
        language = result.get("language") or "en"
        try:
            align_model, metadata = whisperx.load_align_model(language_code=language, device=device)
            result = whisperx.align(result["segments"], align_model, metadata, audio, device, return_char_alignments=False)
        except Exception:
            pass
        with database() as conn:
            for segment in result.get("segments", []):
                text = segment.get("text", "").strip()
                if not text:
                    continue
                cursor = conn.execute(
                    "INSERT INTO transcripts(asset_id,start_ms,end_ms,text,language,words_json) VALUES (?,?,?,?,?,?)",
                    (asset_id, round(segment.get("start", 0) * 1000), round(segment.get("end", 0) * 1000), text, language, json.dumps(segment.get("words", []), ensure_ascii=False)),
                )
                conn.execute(
                    "INSERT INTO search_fts(entity_type,entity_id,asset_id,text) VALUES ('transcript',?,?,?)",
                    (cursor.lastrowid, asset_id, text),
                )
            if result.get("segments"):
                conn.execute("UPDATE assets SET has_speech=1 WHERE id=?", (asset_id,))

    def _process(self, job: dict) -> None:
        job_id = job["id"]
        with database() as conn:
            asset = conn.execute("SELECT * FROM assets WHERE id=?", (job["asset_id"],)).fetchone()
        if not asset:
            return
        source = Path(asset["path"])
        if not source.exists():
            raise FileNotFoundError("Source file is unavailable")
        cache = settings.cache_dir / str(asset["id"])
        cache.mkdir(parents=True, exist_ok=True)
        thumbnail = cache / "cover.webp"
        preview = cache / ("preview.m4a" if asset["media_type"] == "audio" else "preview.mp4")

        self._job_progress(job_id, 0.08, "Inspecting media")
        metadata = probe(source) if asset["media_type"] != "image" else {"duration_ms": 0, "width": None, "height": None, "fps": None, "has_audio": False}
        if asset["media_type"] == "image":
            from PIL import Image
            with Image.open(source) as image:
                metadata["width"], metadata["height"] = image.size

        self._job_progress(job_id, 0.18, "Creating preview")
        if asset["media_type"] == "image":
            image_thumbnail(source, thumbnail)
        elif asset["media_type"] == "video":
            video_thumbnail(source, thumbnail, min(1000, max(0, metadata["duration_ms"] // 2)))
            create_preview(source, preview, "video")
        else:
            audio_waveform(source, thumbnail, metadata["duration_ms"])
            create_preview(source, preview, "audio")

        with database() as conn:
            conn.execute(
                """UPDATE assets SET duration_ms=?,width=?,height=?,fps=?,has_audio=?,thumbnail_path=?,preview_path=?,status='processing',error=NULL
                   WHERE id=?""",
                (metadata["duration_ms"], metadata["width"], metadata["height"], metadata["fps"], int(metadata["has_audio"]), str(thumbnail), str(preview) if preview.exists() else None, asset["id"]),
            )

        self._job_progress(job_id, 0.34, "Detecting scenes")
        scene_rows: list[tuple[int, int, int, Path]] = []
        if asset["media_type"] == "video":
            for number, (start_ms, end_ms) in enumerate(detect_scenes(source, metadata["duration_ms"]), 1):
                scene_thumb = cache / f"scene-{number:05d}.webp"
                midpoint = start_ms + max(0, end_ms - start_ms) // 2
                video_thumbnail(source, scene_thumb, midpoint)
                with database() as conn:
                    cursor = conn.execute(
                        "INSERT INTO scenes(asset_id,scene_number,start_ms,end_ms,thumbnail_path) VALUES (?,?,?,?,?)",
                        (asset["id"], number, start_ms, end_ms, str(scene_thumb)),
                    )
                    scene_rows.append((int(cursor.lastrowid), start_ms, end_ms, scene_thumb))
        elif asset["media_type"] == "image":
            with database() as conn:
                cursor = conn.execute(
                    "INSERT INTO scenes(asset_id,scene_number,start_ms,end_ms,thumbnail_path) VALUES (?,?,?,?,?)",
                    (asset["id"], 1, 0, 0, str(thumbnail)),
                )
                scene_rows.append((int(cursor.lastrowid), 0, 0, thumbnail))

        if metadata["has_audio"] or asset["media_type"] == "audio":
            self._job_progress(job_id, 0.55, "Transcribing speech")
            self._transcribe(asset["id"], source)

        if asset["media_type"] == "video":
            self._job_progress(job_id, 0.64, "Scanning wanted-level HUD")
            self._index_wanted_hud(
                int(asset["id"]), source, int(metadata["width"] or 0), int(metadata["height"] or 0),
                int(metadata["duration_ms"] or 0), scene_rows,
            )

        self._job_progress(job_id, 0.7, "Building semantic index")
        if semantic_index.available:
            ui_records = []
            for scene_id, start_ms, _, scene_thumb in scene_rows:
                semantic_index.add_visual(asset["id"], scene_id, start_ms, scene_thumb)
                if asset["media_type"] == "video":
                    ui_path = cache / "ui" / f"scene-{scene_id:06d}.webp"
                    hud_montage(scene_thumb, ui_path)
                    ui_records.append({
                        "asset_id": int(asset["id"]), "scene_id": scene_id, "frame_id": -1,
                        "start_ms": start_ms, "image_path": str(ui_path),
                    })
            if ui_records:
                semantic_index.replace_visual_ui(int(asset["id"]), ui_records)
            if metadata["has_audio"] or asset["media_type"] == "audio":
                for start_ms, end_ms in audio_windows(metadata["duration_ms"]):
                    with database() as conn:
                        cursor = conn.execute(
                            "INSERT INTO audio_segments(asset_id,start_ms,end_ms) VALUES (?,?,?)",
                            (asset["id"], start_ms, end_ms),
                        )
                    semantic_index.add_audio(asset["id"], int(cursor.lastrowid), start_ms, end_ms, source)

        self._job_progress(job_id, 0.96, "Finalizing")
        with database() as conn:
            conn.execute("UPDATE assets SET status='ready', indexed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE id=?", (asset["id"],))

        if asset['media_type'] == 'video' and settings.vehicle_detection_enabled:
            self.enqueue_vehicles(int(asset['id']))


index_worker = IndexWorker()
