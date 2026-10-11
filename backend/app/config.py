from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    app_name: str = "Footage Manager"
    data_dir: Path = ROOT.parent / "data"
    export_dir: Path = ROOT.parent / "exports"
    scan_interval_seconds: int = 60
    preview_height: int = 720
    semantic_search_enabled: bool = True
    transcription_enabled: bool = True
    whisper_model: str = "large-v3-turbo"
    vehicle_detection_enabled: bool = True
    vehicle_sample_ms: int = 500
    vehicle_confidence: float = 0.45
    vehicle_track_sample_ms: int = 200
    vehicle_track_gap_ms: int = 2000
    vehicle_crop_padding: float = 0.125
    visual_model: str = "google/siglip2-base-patch16-224"
    audio_model: str = "laion/larger_clap_general"
    translation_model: str = "Helsinki-NLP/opus-mt-ru-en"
    model_config = SettingsConfigDict(env_prefix="FOOTAGE_", env_file=".env", extra="ignore")

    @property
    def database_path(self) -> Path:
        return self.data_dir / "footage.db"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def vector_dir(self) -> Path:
        return self.data_dir / "vectors"

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.vector_dir.mkdir(parents=True, exist_ok=True)
        self.export_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
