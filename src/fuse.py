from dataclasses import dataclass, field
from typing import List, Optional

from src.audio_match import AudioMatch
from src.config import AMBIGUITY_SCORE_MARGIN, CONFIRMATION_WINDOW_SEC, CONTIGUOUS_GAP_FRAMES
from src.ocr_match import FrameMatch


@dataclass
class FusionResult:
    status: str
    frame_match: Optional[FrameMatch] = None
    audio_confirmed: Optional[bool] = None
    confirming_audio_text: Optional[str] = None
    ambiguous_visual_candidates: List[FrameMatch] = field(default_factory=list)
    unconfirmed_audio_hits: List[AudioMatch] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def _cluster_by_frame_gap(matches: List[FrameMatch], max_gap: int) -> List[List[FrameMatch]]:
    if not matches:
        return []
    clusters = [[matches[0]]]
    for m in matches[1:]:
        if m.frame_number - clusters[-1][-1].frame_number <= max_gap:
            clusters[-1].append(m)
        else:
            clusters.append([m])
    return clusters


def fuse(visual_matches: List[FrameMatch], audio_matches: List[AudioMatch], fps: float) -> FusionResult:
    result = FusionResult(status="not_found")

    if not visual_matches:
        if audio_matches:
            result.unconfirmed_audio_hits = audio_matches[:5]
            result.notes.append("Dialogue was detected in speech but no matching on-screen text "
                "was found anywhere in the video - it may be spoken only, never shown on screen, or OCR missed it (check ambiguous/low-quality frames).")
        else:
            result.notes.append("No match found in either the visual or audio branch.")
        return result

    visual_matches = sorted(visual_matches, key=lambda m: m.frame_number)
    clusters = _cluster_by_frame_gap(visual_matches, CONTIGUOUS_GAP_FRAMES)
    best_cluster = clusters[0]
    best = best_cluster[0]
    result.status = "found"
    result.frame_match = best

    best_time_sec = best.frame_number / fps
    confirming = [a for a in audio_matches
        if (a.start_sec - CONFIRMATION_WINDOW_SEC) <= best_time_sec <= (a.end_sec + CONFIRMATION_WINDOW_SEC)]

    if confirming:
        result.audio_confirmed = True
        result.confirming_audio_text = confirming[0].text
        result.notes.append("Visual match is corroborated by spoken audio in the same time window.")
    elif audio_matches:
        result.audio_confirmed = False
        result.unconfirmed_audio_hits = audio_matches[:5]
        result.notes.append(
            "Visual match found, but it does not align in time with any matching "
            "spoken-audio segment - the line may be on-screen text only."
        )
    else:
        result.audio_confirmed = None
        result.notes.append("No audio branch data to cross-check against (no speech match, no audio track, or audio branch skipped).")

    other_cluster_matches = [m for cluster in clusters[1:] for m in cluster]
    others = [m for m in other_cluster_matches if abs(m.score - best.score) <= AMBIGUITY_SCORE_MARGIN]
    if others:
        result.ambiguous_visual_candidates = others[:5]
        result.notes.append(
            f"{len(others)} frame(s) in {len(clusters) - 1} separate, later occurrence(s) "
            f"scored within {AMBIGUITY_SCORE_MARGIN} points of the best match - the dialogue may reappear elsewhere in the video.")

    return result