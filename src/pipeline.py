import argparse
import json
import os
import sys
import threading

import cv2

from src.audio_match import transcribe_and_match
from src.av_probe import has_audio_stream
from src.config import (AUDIO_BRANCH_TIMEOUT_SEC,AUDIO_HINT_WINDOW_PADDING_SEC,FINE_WINDOW_PADDING_SEC,
                        OUTPUT_DIR,WHISPER_MODEL_SIZE)
from src.download import download_video
from src.fuse import fuse
from src.ocr_match import extract_frame_at, fine_scan_window, full_video_fine_scan
from src.text_detect import coarse_scan_iter, probe_fps_frames


def _resolve_video_path(url: str, video_path: str) -> str:
    if video_path:
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"--video-path given but file does not exist: {video_path}")
        return video_path
    if not url:
        raise ValueError("Provide either --video-path (preferred, avoids re-downloading) or --url")
    return download_video(url)


def run_pipeline(url: str = None,video_path: str = None,
                 target_text: str = None ,skip_audio: bool = False,
                 whisper_model_size: str = WHISPER_MODEL_SIZE,output_dir: str = OUTPUT_DIR) -> dict:
    os.makedirs(output_dir, exist_ok=True)
    log = []

    video_path = _resolve_video_path(url, video_path)
    log.append(f"Using video file: {video_path}")

    run_audio = not skip_audio
    if run_audio and not has_audio_stream(video_path):
        run_audio = False
        log.append("No audio stream detected in this video - audio branch skipped.")
    elif skip_audio:
        log.append("Audio branch disabled via --skip-audio.")

    fps, total_frames = probe_fps_frames(video_path)
    log.append(f"Video: fps={fps:.3f}, total_frames={total_frames}")

    audio_result = {}
    audio_found_event = threading.Event()
    audio_stop_not_before = {"value": None}

    def _run_audio():
        matches, note = transcribe_and_match(video_path, target_text, whisper_model_size)
        audio_result["value"] = (matches, note)
        if matches:
            audio_stop_not_before["value"] = matches[0].start_sec
            audio_found_event.set()

    audio_thread = None
    if run_audio:
        audio_thread = threading.Thread(target=_run_audio, daemon=True)
        audio_thread.start()

    visual_matches = []
    coarse_hit_count = 0
    for hit in coarse_scan_iter(video_path, target_text, stop_event=audio_found_event, 
                                stop_not_before=audio_stop_not_before):
        coarse_hit_count += 1
        s = max(0.0, hit.time_sec - FINE_WINDOW_PADDING_SEC)
        e = hit.time_sec + FINE_WINDOW_PADDING_SEC
        window_matches = fine_scan_window(video_path, target_text, fps, s, e)
        if window_matches:
            visual_matches = window_matches
            log.append(f"Coarse hit #{coarse_hit_count} at {hit.time_sec:.2f}s fine-scan-confirmed "
                       f"a match - stopping the coarse scan early.")
            break
    else:
        if audio_found_event.is_set():
            log.append(f"Coarse visual scan stopped after {coarse_hit_count} candidate sample(s) - "
                "audio branch confirmed a match first.")
        else:
            log.append(f"Coarse visual scan examined {coarse_hit_count} candidate sample(s), none confirmed.")

    if audio_thread is not None:
        audio_thread.join(timeout=AUDIO_BRANCH_TIMEOUT_SEC)
        if audio_thread.is_alive():
            audio_matches, audio_note = [], (f"audio branch still transcribing after {AUDIO_BRANCH_TIMEOUT_SEC:.0f}s - "
                "proceeding without it (it is cross-check only, never required for the answer)")
        else:
            audio_matches, audio_note = audio_result["value"]
    else:
        audio_matches, audio_note = [], "audio branch not run"
    log.append(f"[audio branch] {audio_note}")

    earliest_visual_time = visual_matches[0].frame_number / fps if visual_matches else None
    for a in audio_matches:
        if earliest_visual_time is not None and a.start_sec >= earliest_visual_time:
            continue
        s = max(0.0, a.start_sec - AUDIO_HINT_WINDOW_PADDING_SEC)
        e = a.end_sec + AUDIO_HINT_WINDOW_PADDING_SEC
        audio_window_matches = fine_scan_window(video_path, target_text, fps, s, e)
        if audio_window_matches:
            log.append(f"Audio hint at {a.start_sec:.2f}s fine-scan-confirmed an earlier visual match.")
            visual_matches = audio_window_matches
            earliest_visual_time = visual_matches[0].frame_number / fps

    if not visual_matches and audio_matches:
        best_audio = audio_matches[0]
        audio_frame = extract_frame_at(video_path, fps, best_audio.start_sec, best_audio.text, best_audio.score)
        if audio_frame is not None:
            visual_matches = [audio_frame]
            log.append(f"No on-screen text found for the spoken match at {best_audio.start_sec:.2f}s - "
                "extracting that frame anyway as a best-effort answer (audio-derived, not OCR-confirmed).")

    if not visual_matches and not audio_matches:
        log.append(
            "No candidate windows from either branch - falling back to a full "
            "frame-by-frame scan of the entire video (slow)."
        )
        visual_matches = full_video_fine_scan(video_path, target_text, fps, total_frames)

    fusion = fuse(visual_matches, audio_matches, fps)
    log.extend(fusion.notes)

    result = {
        "status": fusion.status,
        "timestamp": None,
        "frame_number": None,
        "text": None,
        "confidence": None,
        "frame_image_path": None,
        "match_source": None,
        "audio_confirmed": fusion.audio_confirmed,
        "confirming_audio_text": fusion.confirming_audio_text,
        "ambiguous_visual_candidates": [
            {"frame_number": m.frame_number, "timestamp": m.timestamp, "score": m.score, "text": m.raw_text}
            for m in fusion.ambiguous_visual_candidates
        ],
        "unconfirmed_audio_hits": [
            {"start_sec": round(a.start_sec, 3), "end_sec": round(a.end_sec, 3), "score": a.score, "text": a.text}
            for a in fusion.unconfirmed_audio_hits
        ],
        "log": log,
    }

    if fusion.status == "found":
        best = fusion.frame_match
        result["timestamp"] = best.timestamp
        result["frame_number"] = best.frame_number
        result["text"] = best.raw_text
        result["confidence"] = best.score
        result["match_source"] = "on_screen_text" if best.source == "visual" else "spoken_audio"

        frame_out_path = os.path.join(output_dir, "matched_frame.png")
        cv2.imwrite(frame_out_path, best.frame_image)
        result["frame_image_path"] = frame_out_path

    with open(os.path.join(output_dir, "result.json"), "w") as f:
        json.dump(result, f, indent=2)

    return result


def format_result(result: dict) -> str:
    if result["status"] != "found":
        lines = ["No matching frame found."]
        if result.get("unconfirmed_audio_hits"):
            lines.append(
                "(The dialogue was detected in speech, but no frame could be extracted for it - "
                "see output/result.json for details.)"
            )
        return "\n".join(lines)

    lines = [
        f"{'Timestamp':<9} : {result['timestamp']}",
        f"{'Frame':<9} : {result['frame_number']}",
        f"{'Text':<9} : \"{result['text']}\"",
    ]
    if result.get("match_source") == "spoken_audio":
        lines.append(
            f"{'Note':<9} : frame captured at the moment the line was spoken - "
            "matching text was not found rendered on screen."
        )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Find the exact frame where an on-screen dialogue first appears, cross-checked with audio.")
    parser.add_argument("--url", help="Video URL (only used if --video-path is not given)")
    parser.add_argument("--video-path", help="Local video file - preferred, skips any download")
    parser.add_argument("--text", required=True, help="Target dialogue text to search for")
    parser.add_argument("--skip-audio", action="store_true", help="Disable the audio/ASR branch entirely")
    parser.add_argument("--whisper-model", default=WHISPER_MODEL_SIZE,
        help="faster-whisper model size: tiny/base/small/medium/large-v3 (default: base)")
    parser.add_argument("--output-dir", default=OUTPUT_DIR)
    parser.add_argument("--json", action="store_true", help="Print the full JSON result instead of the human-readable summary")
    args = parser.parse_args()

    result = run_pipeline(url=args.url,video_path=args.video_path,target_text=args.text,skip_audio=args.skip_audio,
                          whisper_model_size=args.whisper_model,output_dir=args.output_dir)

    print(json.dumps(result, indent=2) if args.json else format_result(result))
    if result["status"] != "found":
        sys.exit(1)

if __name__ == "__main__":
    main()