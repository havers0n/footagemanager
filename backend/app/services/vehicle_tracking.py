"""Tracking adapters and explicit, inspectable crop quality/similarity heuristics."""
from __future__ import annotations

from types import SimpleNamespace
import cv2
import numpy as np


def overlap(a, b) -> float:
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = max(0, a[2] - a[0]) * max(0, a[3] - a[1]) + max(0, b[2] - b[0]) * max(0, b[3] - b[1]) - intersection
    return intersection / union if union > 0 else 0.0


def crop_box(box, width: int, height: int):
    x1, y1, x2, y2 = box
    result = (max(0, int(x1)), max(0, int(y1)), min(width, int(np.ceil(x2))), min(height, int(np.ceil(y2))))
    return result if result[2] - result[0] >= 40 and result[3] - result[1] >= 30 else None


def padded_box(box, width, height, padding=0.125):
    x1, y1, x2, y2 = box
    dx, dy = (x2 - x1) * padding, (y2 - y1) * padding
    return [max(0, int(np.floor(x1 - dx))), max(0, int(np.floor(y1 - dy))),
            min(width, int(np.ceil(x2 + dx))), min(height, int(np.ceil(y2 + dy)))]


def occlusion_fraction(box, others):
    """Union of intersecting detection rectangles: an occlusion proxy, not a depth estimate."""
    x1, y1, x2, y2 = box
    rectangles = [(max(x1, b[0]), max(y1, b[1]), min(x2, b[2]), min(y2, b[3])) for b in others]
    rectangles = [b for b in rectangles if b[2] > b[0] and b[3] > b[1]]
    xs = sorted({x for b in rectangles for x in (b[0], b[2])})
    area = 0.0
    for left, right in zip(xs, xs[1:]):
        segments = sorted((b[1], b[3]) for b in rectangles if b[0] < right and b[2] > left)
        end, covered = -float('inf'), 0.0
        for low, high in segments:
            covered += max(0, high - max(low, end))
            end = max(end, high)
        area += (right - left) * covered
    return min(1.0, area / max(1, (x2 - x1) * (y2 - y1)))


def quality(frame, box, confidence, others):
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = box
    crop = frame[y1:y2, x1:x2]
    # Normalize evaluation resolution, so the same texture is not favored purely by pixel count.
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    scale = 160 / max(gray.shape)
    gray = cv2.resize(gray, (max(2, round(gray.shape[1] * scale)), max(2, round(gray.shape[0] * scale))))
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    sharp = sharpness / (sharpness + 150)
    size = min(1.0, np.sqrt((x2 - x1) * (y2 - y1) / (width * height)) / .5)
    margin = min(x1 / max(1, x2 - x1), y1 / max(1, y2 - y1),
                 (width - x2) / max(1, x2 - x1), (height - y2) / max(1, y2 - y1))
    edge = float(np.clip(margin / .04, 0, 1))
    occlusion = occlusion_fraction(box, others)
    score = .25 * size + .20 * confidence + .25 * sharp + .15 * edge + .15 * (1 - occlusion)
    return float(score), {'area_pixels': (x2 - x1) * (y2 - y1), 'size': float(size),
                          'confidence': float(confidence), 'sharpness': sharpness,
                          'sharpness_score': sharp, 'edge_clearance': edge,
                          'occlusion_estimate': occlusion, 'score': float(score)}


class VehicleTracker:
    """ByteTrack: Kalman motion + global association + low-confidence recovery."""
    def __init__(self, interval_ms, gap_ms, confidence):
        from ultralytics.trackers.byte_tracker import BYTETracker
        self.tracker = BYTETracker(SimpleNamespace(track_high_thresh=confidence,
            track_low_thresh=.10, new_track_thresh=confidence,
            track_buffer=max(1, round(gap_ms / interval_ms)), match_thresh=.85, fuse_score=True))

    def update(self, boxes, frame):
        from ultralytics.engine.results import Boxes
        # Retain the detection index so saved bounds are measured, never predicted by the Kalman filter.
        detections = Boxes(np.asarray(boxes, dtype=np.float32).reshape(-1, 6), frame.shape[:2])
        self.tracker.update(detections, frame)
        return [(int(t.track_id), int(t.idx), bool(t.is_activated)) for t in self.tracker.tracked_stracks
                if t.frame_id == self.tracker.frame_id]


def descriptor(crop):
    gray = cv2.cvtColor(cv2.resize(crop, (32, 32)), cv2.COLOR_BGR2GRAY)
    dct = cv2.dct(gray.astype(np.float32))[:8, :8].flatten()[1:]
    bits = dct > np.median(dct)
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [16, 8], [0, 180, 0, 256])
    cv2.normalize(hist, hist)
    return bits, hist.flatten(), crop.shape[1] / crop.shape[0]


def similarity(a, b):
    hash_distance = int(np.count_nonzero(a[0] != b[0]))
    color_distance = float(cv2.compareHist(a[1], b[1], cv2.HISTCMP_BHATTACHARYYA))
    ratio_distance = abs(np.log(a[2] / b[2]))
    if hash_distance > 12 or color_distance > .32 or ratio_distance > .35:
        return None
    return float(.65 * (1 - hash_distance / 63) + .35 * (1 - color_distance))


def group_similar(records):
    """Complete-link groups across distinct scenes; never discard or collapse tracks."""
    groups = []
    for record in records:
        record['group_id'] = None
        record['similarity_json'] = []
        for group in groups:
            scores = [similarity(record['descriptor'], other['descriptor'])
                      if record['scene_number'] != other['scene_number'] and record['category'] == other['category']
                      else None for other in group]
            if all(value is not None for value in scores):
                group.append(record)
                for other, score in zip(group[:-1], scores):
                    record['similarity_json'].append({'track_id': other['track_id'], 'score': score})
                    other['similarity_json'].append({'track_id': record['track_id'], 'score': score})
                break
        else:
            groups.append([record])
    for group in groups:
        if len(group) > 1:
            for record in group:
                record['group_id'] = 'possible-' + group[0]['track_id']
