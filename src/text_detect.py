from dataclasses import dataclass
from typing import List, Tuple

import cv2
import pytesseract
from rapidfuzz import fuzz

from src.config import (COARSE_DOWNSCALE_WIDTH,COARSE_FUZZY_THRESHOLD,COARSE_MIN_CHAR_COUNT,
                        COARSE_SAMPLE_FPS,MIN_OCR_LEN_RATIO,TESSERACT_CONFIG)
from src.utils import normalize_text


@dataclass
class CoarseHit:
    time_sec: float
    score: int
    raw_text: str


def _downscale(frame, target_width: int):
    h, w = frame.shape[:2]
    if w <= target_width:
        return frame
    scale = target_width / w
    return cv2.resize(frame, (target_width, int(h * scale)), interpolation=cv2.INTER_AREA)


def probe_fps_frames(video_path: str) -> Tuple[float, int]:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise IOError(f"Could not open video: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    return fps, total_frames


def coarse_scan_iter(video_path: str, target_text: str, stop_event=None, stop_not_before=None):
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise IOError(f"Could not open video: {video_path}")

    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration_sec = total_frames / fps if fps else 0

        sample_interval_sec = 1.0 / COARSE_SAMPLE_FPS
        target_norm = normalize_text(target_text)
        min_len = max(COARSE_MIN_CHAR_COUNT, int(len(target_norm) * MIN_OCR_LEN_RATIO))

        t = 0.0
        while t < duration_sec:
            if stop_event is not None and stop_event.is_set():
                limit = stop_not_before.get("value") if stop_not_before else None
                if limit is None or t >= limit:
                    break
            frame_idx = int(t * fps)
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ok, frame = cap.read()
            if not ok:
                break

            small = _downscale(frame, COARSE_DOWNSCALE_WIDTH)
            raw_text = pytesseract.image_to_string(small, config=TESSERACT_CONFIG)
            norm = normalize_text(raw_text)

            if len(norm) >= min_len:
                score = fuzz.partial_ratio(target_norm, norm)
                if score >= COARSE_FUZZY_THRESHOLD:
                    yield CoarseHit(time_sec=t, score=score, raw_text=raw_text.strip())

            t += sample_interval_sec
    finally:
        cap.release()


def coarse_scan(video_path: str, target_text: str) -> Tuple[List[CoarseHit], float, int]:
    fps, total_frames = probe_fps_frames(video_path)
    hits = list(coarse_scan_iter(video_path, target_text))
    hits.sort(key=lambda h: -h.score)
    return hits, fps, total_frames