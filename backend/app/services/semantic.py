from __future__ import annotations

import re
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any

import numpy as np

from ..config import settings
from .media import require_binary


CYRILLIC = re.compile(r"[\u0400-\u04ff]")


class SemanticIndex:
    """Lazy local multimodal models and embedded LanceDB storage.

    The app remains usable without AI dependencies. Once the optional bundle is
    installed, the first indexing/search call downloads and caches the models.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._db: Any = None
        self._visual_model: Any = None
        self._visual_processor: Any = None
        self._audio_model: Any = None
        self._audio_processor: Any = None
        self._translator: tuple[Any, Any] | None = None

    @property
    def available(self) -> bool:
        try:
            import lancedb  # noqa: F401
            import torch  # noqa: F401
            import transformers  # noqa: F401
            return settings.semantic_search_enabled
        except ImportError:
            return False

    def _connect(self):
        if self._db is None:
            import lancedb

            settings.ensure_directories()
            self._db = lancedb.connect(str(settings.vector_dir))
        return self._db

    @staticmethod
    def _device() -> str:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"

    def _load_visual(self) -> None:
        if self._visual_model is not None:
            return
        from transformers import AutoModel, AutoProcessor

        self._visual_processor = AutoProcessor.from_pretrained(settings.visual_model)
        self._visual_model = AutoModel.from_pretrained(settings.visual_model).to(self._device()).eval()

    def _load_audio(self) -> None:
        if self._audio_model is not None:
            return
        from transformers import AutoModel, AutoProcessor

        self._audio_processor = AutoProcessor.from_pretrained(settings.audio_model)
        self._audio_model = AutoModel.from_pretrained(settings.audio_model).to(self._device()).eval()

    @staticmethod
    def _normalized(value: Any) -> list[float]:
        import torch

        value = value.detach().float().cpu()
        value = torch.nn.functional.normalize(value, dim=-1)
        return value[0].numpy().astype("float32").tolist()

    @staticmethod
    def _normalized_batch(value: Any) -> list[list[float]]:
        import torch

        value = value.detach().float().cpu()
        value = torch.nn.functional.normalize(value, dim=-1)
        return value.numpy().astype("float32").tolist()

    def visual_image_vector(self, path: Path) -> list[float]:
        from PIL import Image
        import torch

        with self._lock:
            self._load_visual()
            with Image.open(path) as image:
                inputs = self._visual_processor(images=image.convert("RGB"), return_tensors="pt").to(self._device())
            with torch.inference_mode():
                features = self._visual_model.get_image_features(**inputs)
            return self._normalized(features)

    def visual_image_vectors(self, paths: list[Path]) -> list[list[float]]:
        if not paths:
            return []
        from PIL import Image
        import torch

        with self._lock:
            self._load_visual()
            images = []
            for path in paths:
                with Image.open(path) as image:
                    images.append(image.convert("RGB"))
            inputs = self._visual_processor(images=images, return_tensors="pt").to(self._device())
            with torch.inference_mode():
                features = self._visual_model.get_image_features(**inputs)
            return self._normalized_batch(features)

    def visual_text_vector(self, text: str) -> list[float]:
        import torch

        with self._lock:
            self._load_visual()
            inputs = self._visual_processor(text=[text], padding="max_length", max_length=64, return_tensors="pt").to(self._device())
            with torch.inference_mode():
                features = self._visual_model.get_text_features(**inputs)
            return self._normalized(features)

    def visual_text_vectors(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        import torch

        with self._lock:
            self._load_visual()
            inputs = self._visual_processor(
                text=texts, padding="max_length", max_length=64, return_tensors="pt"
            ).to(self._device())
            with torch.inference_mode():
                features = self._visual_model.get_text_features(**inputs)
            return self._normalized_batch(features)

    def translate_to_english(self, text: str) -> str:
        if not CYRILLIC.search(text):
            return text
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        with self._lock:
            if self._translator is None:
                tokenizer = AutoTokenizer.from_pretrained(settings.translation_model)
                model = AutoModelForSeq2SeqLM.from_pretrained(settings.translation_model).to(self._device()).eval()
                self._translator = tokenizer, model
            tokenizer, model = self._translator
            batch = tokenizer([text], return_tensors="pt", truncation=True).to(self._device())
            with torch.inference_mode():
                output = model.generate(**batch, max_new_tokens=80)
            return tokenizer.batch_decode(output, skip_special_tokens=True)[0]

    def audio_vector(self, source: Path, start_ms: int, end_ms: int) -> list[float]:
        import librosa
        import torch

        ffmpeg = require_binary("ffmpeg")
        with tempfile.NamedTemporaryFile(suffix=".wav") as temp:
            subprocess.run([
                ffmpeg, "-y", "-ss", f"{start_ms / 1000:.3f}", "-i", str(source),
                "-t", f"{(end_ms - start_ms) / 1000:.3f}", "-ac", "1", "-ar", "48000", temp.name,
            ], capture_output=True, check=True)
            samples, _ = librosa.load(temp.name, sr=48_000, mono=True)
        with self._lock:
            self._load_audio()
            inputs = self._audio_processor(audios=samples, sampling_rate=48_000, return_tensors="pt").to(self._device())
            with torch.inference_mode():
                features = self._audio_model.get_audio_features(**inputs)
            return self._normalized(features)

    def audio_text_vector(self, text: str) -> list[float]:
        import torch

        translated = self.translate_to_english(text)
        with self._lock:
            self._load_audio()
            inputs = self._audio_processor(text=[translated], padding=True, return_tensors="pt").to(self._device())
            with torch.inference_mode():
                features = self._audio_model.get_text_features(**inputs)
            return self._normalized(features)

    def _append(self, table_name: str, record: dict[str, Any]) -> None:
        db = self._connect()
        if table_name in db.table_names():
            db.open_table(table_name).add([record])
        else:
            db.create_table(table_name, data=[record])

    def add_visual(self, asset_id: int, scene_id: int | None, start_ms: int, image_path: Path) -> None:
        vector = self.visual_image_vector(image_path)
        self._append("visual", {"asset_id": asset_id, "scene_id": scene_id or -1, "start_ms": start_ms, "vector": vector})

    def add_visual_frame(self, asset_id: int, scene_id: int, frame_id: int, timestamp_ms: int, image_path: Path) -> None:
        vector = self.visual_image_vector(image_path)
        self._append("visual_frames", {
            "asset_id": asset_id,
            "scene_id": scene_id,
            "frame_id": frame_id,
            "start_ms": timestamp_ms,
            "vector": vector,
        })

    def clear_visual_frames(self, asset_id: int) -> None:
        db = self._connect()
        if "visual_frames" in db.table_names():
            db.open_table("visual_frames").delete(f"asset_id = {int(asset_id)}")

    def replace_visual_ui(self, asset_id: int, records: list[dict[str, Any]], batch_size: int = 16) -> None:
        db = self._connect()
        if "visual_ui" in db.table_names():
            db.open_table("visual_ui").delete(f"asset_id = {int(asset_id)}")
        for offset in range(0, len(records), batch_size):
            batch = records[offset:offset + batch_size]
            vectors = self.visual_image_vectors([Path(record["image_path"]) for record in batch])
            payload = [
                {
                    "asset_id": record["asset_id"],
                    "scene_id": record["scene_id"],
                    "frame_id": record["frame_id"],
                    "start_ms": record["start_ms"],
                    "vector": vector,
                }
                for record, vector in zip(batch, vectors, strict=True)
            ]
            if "visual_ui" in db.table_names():
                db.open_table("visual_ui").add(payload)
            else:
                db.create_table("visual_ui", data=payload)

    def add_audio(self, asset_id: int, segment_id: int, start_ms: int, end_ms: int, source: Path) -> None:
        vector = self.audio_vector(source, start_ms, end_ms)
        self._append("audio", {"asset_id": asset_id, "segment_id": segment_id, "start_ms": start_ms, "end_ms": end_ms, "vector": vector})

    def search(self, table_name: str, vector: list[float], limit: int = 80) -> list[dict[str, Any]]:
        db = self._connect()
        if table_name not in db.table_names():
            return []
        rows = db.open_table(table_name).search(np.asarray(vector, dtype="float32")).metric("cosine").limit(limit).to_list()
        return rows


semantic_index = SemanticIndex()
