import subprocess
from pathlib import Path

import pytest

from app.services import media
from app.services.media import MediaToolError, audio_windows, create_preview, export_frame, run, safe_export_name, safe_screenshot_name


def test_audio_windows_overlap_and_tail():
    assert audio_windows(21_000) == [(0, 10_000), (5_000, 15_000), (10_000, 20_000), (15_000, 21_000)]
    assert audio_windows(3_000) == [(0, 3_000)]


def test_safe_export_name():
    assert safe_export_name("Lucia: police / chase", 1200, 4900) == "Lucia__police___chase_000001200-000004900.mp4"


def test_safe_screenshot_name():
    assert safe_screenshot_name("Lucia: police / chase", 933_167, 7) == "Lucia__police___chase_frame_000933167_7.png"


def test_export_frame_uses_accurate_timestamp_and_png(monkeypatch, tmp_path):
    captured: list[str] = []
    monkeypatch.setattr(media, "require_binary", lambda _: "ffmpeg")
    monkeypatch.setattr(media, "run", lambda command: captured.extend(command))

    export_frame(Path("source.mp4"), tmp_path / "frame.png", 933_167)

    assert captured[captured.index("-ss") + 1] == "933.167000"
    assert captured[captured.index("-frames:v") + 1] == "1"
    assert captured[captured.index("-c:v") + 1] == "png"


def test_video_preview_escapes_scale_expression(monkeypatch, tmp_path):
    captured: list[str] = []
    monkeypatch.setattr(media, "require_binary", lambda _: "ffmpeg")
    monkeypatch.setattr(media, "run", lambda command: captured.extend(command))

    create_preview(Path("source.mp4"), tmp_path / "preview.mp4", "video")

    assert "scale=-2:min(720\\,ih)" in captured


def test_run_reports_end_of_ffmpeg_error(monkeypatch):
    stderr = "ffmpeg banner\n" + ("configuration noise\n" * 300) + "No such filter: ih)"
    monkeypatch.setattr(
        media.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", stderr),
    )

    with pytest.raises(MediaToolError, match=r"No such filter"):
        run(["ffmpeg", "-i", "source.mp4"])
