from __future__ import annotations

import pytest

from app.config import settings
from app.db import initialize_database


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "export_dir", tmp_path / "exports")
    monkeypatch.setattr(settings, "semantic_search_enabled", False)
    monkeypatch.setattr(settings, "transcription_enabled", False)
    settings.ensure_directories()
    initialize_database()
    yield tmp_path

