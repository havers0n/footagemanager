from __future__ import annotations

import math

import cv2
import numpy as np
from PIL import Image

from app.services.media import detect_wanted_stars, hud_montage, merge_wanted_observations


def _star(center_x: int, center_y: int, outer: int = 24, inner: int = 10) -> np.ndarray:
    points = []
    for index in range(10):
        angle = -math.pi / 2 + index * math.pi / 5
        radius = outer if index % 2 == 0 else inner
        points.append([round(center_x + math.cos(angle) * radius), round(center_y + math.sin(angle) * radius)])
    return np.asarray(points, dtype=np.int32)


def test_detects_aligned_bright_wanted_stars():
    frame = np.zeros((360, 960, 3), dtype=np.uint8)
    for center_x in (730, 790, 850, 910):
        cv2.fillPoly(frame, [_star(center_x, 55)], (255, 255, 255))
        cv2.circle(frame, (center_x, 105), 14, (30, 30, 190), -1)

    count, confidence = detect_wanted_stars(frame)

    assert count == 4
    assert confidence >= 0.9


def test_merges_consecutive_equal_wanted_levels():
    merged = merge_wanted_observations(
        [(0, 4, 0.95), (500, 4, 0.97), (1_000, 4, 0.96), (2_000, 3, 0.9)],
        duration_ms=3_000,
    )

    assert merged[0][:3] == (0, 1_500, 4)
    assert merged[1][:3] == (1_750, 2_500, 3)


def test_hud_montage_magnifies_all_four_corners(tmp_path):
    source = tmp_path / "source.png"
    output = tmp_path / "hud.webp"
    image = Image.new("RGB", (400, 200), "black")
    image.paste("red", (0, 0, 80, 60))
    image.paste("green", (320, 0, 400, 60))
    image.paste("blue", (0, 140, 80, 200))
    image.paste("yellow", (320, 140, 400, 200))
    image.save(source)

    hud_montage(source, output)

    with Image.open(output) as montage:
        assert montage.size == (640, 360)
        assert montage.getpixel((20, 20))[0] > 180
        assert montage.getpixel((620, 20))[1] > 70
        assert montage.getpixel((20, 340))[2] > 150
        assert montage.getpixel((620, 340))[0] > 150
