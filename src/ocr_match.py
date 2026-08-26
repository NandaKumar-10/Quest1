from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import pytesseract
from rapidfuzz import fuzz

from src.config import (
    FINE_FUZZY_THRESHOLD,
    FINE_SAMPLE_EVERY_N_FRAMES,
    MIN_OCR_LEN_RATIO,
    TESSERACT_CONFIG,
)
from src.utils import frame_to_timestamp, normalize_text

@dataclass
class FrameMatch:
    frame_number: int
    timestamp: str
    score: int
    raw_text: str
    frame_image: "cv2.Mat"
    source: str = "visual"


def fine_scan_window(video_path: str,target_text: str,fps: float,start_sec: float,end_sec: float) -> List[FrameMatch]:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise IOError(f"Could not open video: {video_path}")

    start_frame = max(0, int(start_sec * fps))
    end_frame = int(end_sec * fps)
    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

    target_norm = normalize_text(target_text)
    min_len = max(1, int(len(target_norm) * MIN_OCR_LEN_RATIO))
    matches: List[FrameMatch] = []

    try:
        frame_idx = start_frame
        while frame_idx <= end_frame:
            ok, frame = cap.read()
            if not ok:
                break

            if (frame_idx - start_frame) % FINE_SAMPLE_EVERY_N_FRAMES == 0:
                raw_text = pytesseract.image_to_string(frame, config=TESSERACT_CONFIG)
                norm = normalize_text(raw_text)
                if len(norm) >= min_len:
                    score = fuzz.partial_ratio(target_norm, norm)
                    if score >= FINE_FUZZY_THRESHOLD:
                        matches.append(
                            FrameMatch(frame_number=frame_idx,timestamp=frame_to_timestamp(frame_idx, fps),score=score,
                                       raw_text=raw_text.strip(),frame_image=frame.copy()))

            frame_idx += 1
    finally:
        cap.release()

    matches.sort(key=lambda m: m.frame_number)
    return matches


def extract_frame_at(video_path: str, fps: float, time_sec: float, text: str, score: int) -> Optional[FrameMatch]:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise IOError(f"Could not open video: {video_path}")

    try:
        frame_idx = max(0, int(time_sec * fps))
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ok, frame = cap.read()
        if not ok:
            return None
        return FrameMatch(frame_number=frame_idx,timestamp=frame_to_timestamp(frame_idx, fps),
                          score=score,raw_text=text.strip(),frame_image=frame.copy(),source="audio")
    finally:
        cap.release()


def full_video_fine_scan(video_path: str, target_text: str, fps: float, total_frames: int) -> List[FrameMatch]:
    duration_sec = total_frames / fps if fps else 0
    return fine_scan_window(video_path, target_text, fps, 0.0, duration_sec)