from dataclasses import dataclass
from typing import List, Tuple

from rapidfuzz import fuzz

from src.config import AUDIO_FUZZY_THRESHOLD, WHISPER_MODEL_SIZE
from src.utils import normalize_text

try:
    from faster_whisper import WhisperModel
    _WHISPER_AVAILABLE = True
except ImportError:
    _WHISPER_AVAILABLE = False

_model_cache = {}


def _get_model(model_size: str):
    if not _WHISPER_AVAILABLE:
        raise RuntimeError("faster-whisper is not installed - cannot load transcription model")
    if model_size not in _model_cache:
        _model_cache[model_size] = WhisperModel(model_size, device="cpu", compute_type="int8")
    return _model_cache[model_size]


@dataclass
class AudioMatch:
    start_sec: float
    end_sec: float
    score: int
    text: str


def transcribe_and_match(video_path: str,target_text: str,
                         model_size: str = WHISPER_MODEL_SIZE) -> Tuple[List[AudioMatch], str]:
    if not _WHISPER_AVAILABLE:
        return [], "faster-whisper not installed - audio branch skipped"

    target_norm = normalize_text(target_text)
    matches: List[AudioMatch] = []
    seg_count = 0

    try:
        model = _get_model(model_size)
        segments, _info = model.transcribe(video_path, beam_size=5)
        for seg in segments:
            seg_count += 1
            seg_norm = normalize_text(seg.text)
            if not seg_norm:
                continue
            score = fuzz.token_set_ratio(target_norm, seg_norm)
            if score >= AUDIO_FUZZY_THRESHOLD:
                matches.append(
                    AudioMatch(start_sec=seg.start, end_sec=seg.end, score=score, text=seg.text.strip())
                )
                break
    except Exception as exc:
        return [], f"audio transcription failed ({exc}) - continuing on video branch only"

    if seg_count == 0:
        return [], "no speech detected in audio track"
    if not matches:
        return [], f"speech detected ({seg_count} segment(s)) but none matched the target text closely enough"

    return matches, f"found matching audio segment at {matches[0].start_sec:.2f}s (stopped scanning further)"