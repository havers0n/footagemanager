from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw

from ..config import settings


class MediaToolError(RuntimeError):
    pass


def require_binary(name: str) -> str:
    binary = shutil.which(name)
    if not binary:
        raise MediaToolError(f"{name} is not installed. Run scripts/setup-wsl.sh first.")
    return binary


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode:
        stderr = result.stderr.strip()
        raise MediaToolError(stderr[-4000:] if stderr else f"Command failed: {command[0]}")
    return result


def probe(path: Path) -> dict[str, Any]:
    ffprobe = require_binary("ffprobe")
    result = run([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)])
    payload = json.loads(result.stdout)
    streams = payload.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    duration = float(payload.get("format", {}).get("duration") or (video or {}).get("duration") or 0)
    fps = 0.0
    if video:
        rate = video.get("avg_frame_rate", "0/1")
        try:
            numerator, denominator = rate.split("/")
            fps = float(numerator) / max(float(denominator), 1)
        except (ValueError, ZeroDivisionError):
            fps = 0.0
    return {
        "duration_ms": round(duration * 1000),
        "width": video.get("width") if video else None,
        "height": video.get("height") if video else None,
        "fps": fps or None,
        "has_audio": bool(audio),
    }


def detect_scenes(path: Path, duration_ms: int) -> list[tuple[int, int]]:
    if duration_ms <= 0:
        return [(0, 0)]
    try:
        from scenedetect import AdaptiveDetector, detect

        detected = detect(str(path), AdaptiveDetector(), show_progress=False)
        scenes = [(round(start.get_seconds() * 1000), round(end.get_seconds() * 1000)) for start, end in detected]
        return scenes or [(0, duration_ms)]
    except Exception:
        return [(0, duration_ms)]


def image_thumbnail(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        image.thumbnail((640, 360))
        canvas = Image.new("RGB", image.size, "#111318")
        if image.mode == "RGBA":
            canvas.paste(image, mask=image.getchannel("A"))
        else:
            canvas.paste(image.convert("RGB"))
        canvas.save(destination, "WEBP", quality=82, method=4)


def hud_montage(source: Path, destination: Path) -> None:
    """Magnify the four screen corners so tiny HUD elements survive model resize."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as opened:
        image = opened.convert("RGB")
        width, height = image.size
        crop_width = max(1, round(width * 0.42))
        crop_height = max(1, round(height * 0.42))
        boxes = (
            (0, 0, crop_width, crop_height),
            (width - crop_width, 0, width, crop_height),
            (0, height - crop_height, crop_width, height),
            (width - crop_width, height - crop_height, width, height),
        )
        canvas = Image.new("RGB", (640, 360), "#090b0f")
        for index, box in enumerate(boxes):
            tile = image.crop(box).resize((320, 180), Image.Resampling.LANCZOS)
            canvas.paste(tile, ((index % 2) * 320, (index // 2) * 180))
        canvas.save(destination, "WEBP", quality=86, method=4)


def video_thumbnail(source: Path, destination: Path, timestamp_ms: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = require_binary("ffmpeg")
    run([
        ffmpeg, "-y", "-ss", f"{max(timestamp_ms, 0) / 1000:.3f}", "-i", str(source),
        "-frames:v", "1", "-vf", "scale=640:360:force_original_aspect_ratio=decrease",
        "-c:v", "libwebp", "-quality", "82", str(destination),
    ])


def detect_wanted_stars(frame: np.ndarray) -> tuple[int, float]:
    """Detect the aligned row of bright GTA wanted stars in a HUD crop."""
    height, width = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, 220, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates: list[tuple[float, float, float, float]] = []
    max_size = height * 0.22
    for contour in contours:
        x, y, box_width, box_height = cv2.boundingRect(contour)
        if x < width * 0.55 or y > height * 0.33:
            continue
        if not (8 <= box_width <= max_size and 8 <= box_height <= max_size):
            continue
        aspect = box_width / box_height
        if not 0.7 <= aspect <= 1.35:
            continue
        area = cv2.contourArea(contour)
        hull_area = cv2.contourArea(cv2.convexHull(contour))
        if hull_area <= 0:
            continue
        solidity = area / hull_area
        fill = area / (box_width * box_height)
        perimeter = cv2.arcLength(contour, True)
        vertices = len(cv2.approxPolyDP(contour, 0.025 * perimeter, True))
        if 0.30 <= solidity <= 0.68 and 0.15 <= fill <= 0.55 and 8 <= vertices <= 12:
            candidates.append((x + box_width / 2, y + box_height / 2, box_width, box_height))

    if not candidates:
        return 0, 0.0

    best_group: list[tuple[float, float, float, float]] = []
    for candidate in candidates:
        _, center_y, _, candidate_height = candidate
        group = [
            item for item in candidates
            if abs(item[1] - center_y) <= max(candidate_height, item[3]) * 0.45
            and 0.65 <= item[3] / candidate_height <= 1.45
        ]
        if len(group) > len(best_group):
            best_group = group

    best_group.sort(key=lambda item: item[0])
    if len(best_group) > 1:
        median_width = float(np.median([item[2] for item in best_group]))
        gaps = [best_group[index + 1][0] - best_group[index][0] for index in range(len(best_group) - 1)]
        plausible_gaps = [gap for gap in gaps if median_width * 1.05 <= gap <= median_width * 2.4]
        if len(plausible_gaps) < len(gaps):
            return 0, 0.0

    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    red_mask = (
        ((hsv[:, :, 0] < 12) | (hsv[:, :, 0] > 168))
        & (hsv[:, :, 1] > 75)
        & (hsv[:, :, 2] > 55)
    )
    red_support = []
    for center_x, center_y, box_width, box_height in best_group:
        x1 = max(0, int(center_x - box_width))
        x2 = min(width, int(center_x + box_width))
        y1 = max(0, int(center_y + box_height * 0.4))
        y2 = min(height, int(center_y + box_height * 3.4))
        region = red_mask[y1:y2, x1:x2]
        red_support.append(float(region.mean()) if region.size else 0.0)
    if not red_support or sum(red_support) / len(red_support) < 0.04:
        return 0, 0.0

    count = min(len(best_group), 6)
    confidence = min(0.99, 0.68 + count * 0.075)
    return count, confidence


def wanted_hud_observations(
    source: Path,
    width: int,
    height: int,
    duration_ms: int,
    samples_per_second: int = 2,
) -> list[tuple[int, int, float]]:
    """Scan a high-resolution top-right crop and return time, star count, confidence."""
    if width <= 0 or height <= 0 or duration_ms <= 0:
        return []
    ffmpeg = require_binary("ffmpeg")
    crop_width = max(2, round(width * 0.45) // 2 * 2)
    crop_height = max(2, round(height * 0.30) // 2 * 2)
    crop_x = max(0, width - crop_width)
    output_width = 960
    output_height = max(2, round(crop_height * output_width / crop_width) // 2 * 2)
    command = [
        ffmpeg, "-v", "error", "-i", str(source), "-vf",
        f"fps={samples_per_second},crop={crop_width}:{crop_height}:{crop_x}:0,scale={output_width}:{output_height}",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1",
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    frame_size = output_width * output_height * 3
    observations: list[tuple[int, int, float]] = []
    frame_number = 0
    assert process.stdout is not None
    while True:
        data = process.stdout.read(frame_size)
        if len(data) < frame_size:
            break
        frame = np.frombuffer(data, dtype=np.uint8).reshape(output_height, output_width, 3)
        count, confidence = detect_wanted_stars(frame)
        if count:
            timestamp_ms = min(duration_ms - 1, round(frame_number * 1000 / samples_per_second))
            observations.append((timestamp_ms, count, confidence))
        frame_number += 1
    stderr = process.stderr.read().decode(errors="replace") if process.stderr else ""
    return_code = process.wait()
    if return_code:
        raise MediaToolError(stderr[-4000:] or "FFmpeg HUD scan failed")
    return observations


def merge_wanted_observations(
    observations: list[tuple[int, int, float]],
    duration_ms: int,
    sample_interval_ms: int = 500,
) -> list[tuple[int, int, int, float]]:
    """Merge consecutive equal wanted levels into searchable time ranges."""
    if not observations:
        return []
    groups: list[list[tuple[int, int, float]]] = [[observations[0]]]
    for observation in observations[1:]:
        previous = groups[-1][-1]
        if observation[1] == previous[1] and observation[0] - previous[0] <= sample_interval_ms * 2:
            groups[-1].append(observation)
        else:
            groups.append([observation])
    merged = []
    for group in groups:
        start_ms = max(0, group[0][0] - sample_interval_ms // 2)
        end_ms = min(duration_ms, group[-1][0] + sample_interval_ms)
        confidence = sum(item[2] for item in group) / len(group)
        merged.append((start_ms, max(start_ms + 1, end_ms), group[0][1], confidence))
    return merged


def audio_waveform(source: Path, destination: Path, duration_ms: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        ffmpeg = require_binary("ffmpeg")
        run([
            ffmpeg, "-y", "-i", str(source), "-filter_complex",
            "showwavespic=s=640x360:colors=4de0c1,format=rgba", "-frames:v", "1", str(destination),
        ])
    except Exception:
        image = Image.new("RGB", (640, 360), "#111318")
        draw = ImageDraw.Draw(image)
        draw.line([(24, 180), (616, 180)], fill="#4de0c1", width=3)
        image.save(destination, "WEBP", quality=82)


def create_preview(source: Path, destination: Path, media_type: str) -> None:
    if media_type == "image":
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = require_binary("ffmpeg")
    command = [ffmpeg, "-y", "-i", str(source)]
    if media_type == "video":
        command += [
            "-vf", f"scale=-2:min({settings.preview_height}\\,ih)", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "25", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(destination),
        ]
    else:
        command += ["-vn", "-c:a", "aac", "-b:a", "128k", str(destination)]
    run(command)


def safe_export_name(stem: str, start_ms: int, end_ms: int) -> str:
    safe = "".join(char if char.isalnum() or char in "-_" else "_" for char in stem).strip("_")[:80] or "clip"
    return f"{safe}_{start_ms:09d}-{end_ms:09d}.mp4"


def safe_screenshot_name(stem: str, timestamp_ms: int, screenshot_id: int) -> str:
    safe = "".join(char if char.isalnum() or char in "-_" else "_" for char in stem).strip("_")[:80] or "frame"
    return f"{safe}_frame_{timestamp_ms:09d}_{screenshot_id}.png"


def export_frame(source: Path, destination: Path, timestamp_ms: int) -> None:
    """Decode and save the source video frame at the requested presentation time."""
    if timestamp_ms < 0:
        raise ValueError("Frame time cannot be negative")
    destination.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = require_binary("ffmpeg")
    run([
        ffmpeg, "-y", "-ss", f"{timestamp_ms / 1000:.6f}", "-i", str(source),
        "-map", "0:v:0", "-frames:v", "1", "-fps_mode", "passthrough",
        "-c:v", "png", str(destination),
    ])


def export_clip(source: Path, destination: Path, start_ms: int, end_ms: int, mode: str) -> None:
    if end_ms <= start_ms:
        raise ValueError("End time must be after start time")
    destination.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = require_binary("ffmpeg")
    start = f"{start_ms / 1000:.3f}"
    duration = f"{(end_ms - start_ms) / 1000:.3f}"
    if mode == "fast":
        command = [ffmpeg, "-y", "-ss", start, "-i", str(source), "-t", duration, "-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy", "-avoid_negative_ts", "make_zero", str(destination)]
    else:
        command = [
            ffmpeg, "-y", "-ss", start, "-i", str(source), "-t", duration,
            "-map", "0:v:0?", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "17", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(destination),
        ]
    run(command)


def audio_windows(duration_ms: int, window_ms: int = 10_000, overlap_ms: int = 5_000) -> list[tuple[int, int]]:
    if duration_ms <= 0:
        return []
    step = window_ms - overlap_ms
    if duration_ms <= window_ms:
        return [(0, duration_ms)]
    starts = list(range(0, duration_ms - window_ms + 1, step))
    tail_start = starts[-1] + step
    if tail_start < duration_ms and starts[-1] + window_ms < duration_ms:
        starts.append(tail_start)
    return [(start, min(start + window_ms, duration_ms)) for start in starts]
