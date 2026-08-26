# Design Document: Finding the Exact Frame Where a Dialogue Appears

## 1. Problem restatement

Given a video, find the first frame where a specific piece of dialogue
appears, and report its timestamp, frame number, extracted text, and an
image of that frame - without a human scrubbing the video. The dialogue may
be shown as on-screen text, spoken, or both, and the video/target phrase may
change.

## 2. Architecture: video and audio as independent parallel detectors

Earlier iterations of this design treated audio as a mere "hint" to narrow
the visual search. The current architecture treats **video (OCR) and audio
(ASR) as two independent detectors that run in parallel**, whose results
are reconciled by a dedicated fusion stage. This matters because:

- The required output is a frame image, so **visual detection is mandatory
  regardless of audio** - it remains the source of truth for the final
  answer.
- But dialogue may be spoken without being shown on screen, shown without
  being spoken (title cards/quote overlays), or both. Treating audio as a
  first-class independent signal - rather than discarding it once it has
  served as a search-window hint - lets the system **surface disagreement
  between modalities** (e.g. "spoken but never shown on screen") instead of
  silently resolving it one way.

```
                          Video file (local or downloaded)
                                      │
                    ┌─────────────────┴──────────────────┐
                    │                                     │
        ┌───────────▼───────────┐             ┌───────────▼────────────┐
        │  Coarse visual scan    │             │   Audio branch          │
        │  sparse OCR sampling   │             │   ASR (faster-whisper)  │
        │  → candidate windows   │             │   + fuzzy match         │
        └───────────┬───────────┘             └───────────┬────────────┘
                    │                                     │
                    └────────────────┬────────────────────┘
                                      │ merged candidate windows
                          ┌───────────▼────────────┐
                          │  Fine visual scan        │
                          │  every frame, full OCR   │
                          └───────────┬───────────────┘
                                      │
                          ┌───────────▼────────────┐
                          │  Fusion & cross-check    │
                          │  reconcile both branches │
                          └───────────┬───────────────┘
                                      │
                          ┌───────────▼────────────┐
                          │  Final decision           │
                          │  earliest confirmed frame │
                          └────────────────────────────┘
```

Both branches (coarse visual scan and audio transcription) run concurrently
via a thread pool, since each is dominated by C-extension work (Tesseract,
CTranslate2) that releases Python's GIL - real wall-clock parallelism, not
just concurrency on paper.

## 3. How the pipeline determines *where to look*

Two independent signals each contribute candidate time windows to the
expensive frame-accurate scan:

1. **Coarse visual scan** (always runs): frames sampled at ~2/sec,
   downscaled, OCR'd, fuzzy-matched at a loose threshold.
2. **Audio branch** (runs whenever the video has an audio track and
   faster-whisper is available): the full track is transcribed with
   segment-level timestamps; each segment is fuzzy-matched against the
   target phrase at a loose threshold.

Windows from both are padded and merged before the expensive stage, so
overlapping regions aren't scanned twice. If a video has no audio track at
all, the audio branch is skipped up front (checked via `ffprobe`) rather
than attempted and failed.

## 4. How it determines *the relevant (exact) frame*

Within each merged window, every frame is OCR'd at full resolution and
fuzzy-matched at a strict threshold. All matching frames are grouped into
**clusters** by frame proximity - because on-screen text typically persists
for many consecutive frames, and that's one appearance, not many. The
**earliest frame of the earliest cluster** is the answer. Its timestamp is
`frame_number / actual_fps`, read from the video's own metadata rather than
assumed, so the pipeline is correct regardless of the source's frame rate.

If neither branch produces any candidate window, the pipeline falls back to
a full frame-by-frame scan of the entire video slow, but ensures
correctness isn't sacrificed for speed.

## 5. How it extracts the text

- **Visual**: Tesseract OCR (`--psm 6`, single uniform text block) via
  `pytesseract`.
- **Audio**: faster-whisper (CTranslate2 backend), chosen over the reference
  `openai-whisper` package specifically because it doesn't require torch -
  smaller Docker image, faster CPU inference, no GPU dependency.

Both engines' raw output is reported as-is (not "corrected" toward the
target phrase), so a reviewer can see exactly what was read/heard versus
what was searched for.

## 6. Fusion: reconciling the two branches

`src/fuse.py` combines the branches into one answer:

- **Visual match found + a temporally-overlapping audio match** →
  `audio_confirmed: true`, with the confirming spoken text included. Highest
  confidence case.
- **Visual match found, no aligned audio match** (whether or not audio
  matched something *elsewhere*) → `audio_confirmed: false`, with a note
  that the line may be on-screen text only.
- **Visual match found, no audio data at all** (no track / branch
  skipped / nothing transcribed) → `audio_confirmed: null` - distinguished
  from `false` deliberately, since "we checked and it didn't align" and "we
  had nothing to check against" are different statements.
- **No visual match anywhere, but audio matched** → `status: "not_found"`
  for the required frame+image output (since only vision can supply that),
  but the audio hit is still surfaced under `unconfirmed_audio_hits` with an
  explanatory note, rather than silently discarded.
- **Neither branch matches** → `status: "not_found"`, full log of what was
  tried.

## 7. Text matching

OCR and ASR output are both noisy. Rather than exact string comparison, the
pipeline normalizes (lowercase, strip punctuation, collapse whitespace) and
scores similarity with RapidFuzz's `partial_ratio` (0–100). Two thresholds
are used by design: **loose** for candidate-window discovery (don't miss a
real hit), **strict** for the final accept/reject decision (don't report a
false positive as the answer).

## 8. Handling ambiguity / uncertainty

- **Same appearance, not ambiguity**: consecutive matching frames (text
  staying on screen) are clustered together and only the earliest is
  reported — they are not flagged as ambiguous candidates.
- **Genuinely separate occurrences**: if the phrase appears to reoccur later
  in the video (a separate frame cluster scoring close to the best), those
  are surfaced in `ambiguous_visual_candidates` rather than hidden.
- **Cross-modality disagreement** is itself a form of ambiguity, and is
  always reported via `audio_confirmed` / `unconfirmed_audio_hits` rather
  than resolved silently in either direction.
- **Total failure** (`not_found`) never happens silently — the full `log`
  explains what was attempted at every stage, on every run.

## 9. Robustness to a different video / different audio / different phrase

Nothing in the pipeline is specific to the reference video or phrase:

- `--text` is a required argument; there is no hardcoded target string
  outside of a CLI default.
- fps, frame count, and audio-track presence are all probed at runtime
  (`cv2.VideoCapture`, `ffprobe`) rather than assumed.
- The audio branch degrades gracefully and independently at every level: no
  audio track → skipped up front; `faster-whisper` not installed → skipped
  with a note; transcription throws for any reason (corrupt audio, unusual
  codec, model download failure) → caught, logged, and **the video branch
  proceeds unaffected**. Audio can never take down the pipeline.
- If OCR/ASR read *something* but it doesn't match, thresholds are only
  ever fuzzy floors, not exact-string gates, so minor rendering/quality
  differences in a different video don't silently break detection the way
  an exact-match approach would.

## 10. Validation performed

`ok.ru` and `huggingface.co` (where faster-whisper fetches model weights)
are both unreachable from the development sandbox's restricted network, so
two synthetic ground-truth videos were used instead (both included in
`sample_media/`, generated with `ffmpeg`):

1. **`synthetic_test.mp4`** — video only, no audio track. Target phrase
   rendered as on-screen text at exactly frame 150 (t=6.000s, 25fps).
   Result: found frame 150, `audio_confirmed: null` (correctly — no audio
   data existed to check against). See `output/test_no_audio/`.
2. **`synthetic_test_with_audio.mp4`** — same visual overlay, plus a
   synthesized (`espeak-ng`) spoken track saying the same phrase, offset by
   0.5s. Result: found the same frame 150. The audio branch itself could not
   be exercised end-to-end in the sandbox (blocked by the `huggingface.co`
   restriction), but this **validated the graceful-degradation path**: the
   transcription failure was caught, logged with a clear reason, and the
   video branch completed correctly regardless — the exact behavior the
   design targets for "audio may fail or be absent." See
   `output/test_dual_modality/` for the full log showing this.

In your own (non-sandboxed) environment, `huggingface.co` will be reachable
and the audio branch will run for real on the first invocation (downloading
and caching the chosen Whisper model).

## 11. Known limitations / next steps

- Tesseract can struggle with stylized fonts, low-contrast overlays, or
  fade transitions; EasyOCR/PaddleOCR or a cloud OCR API would likely
  improve recall on harder real-world footage — the call is isolated to one
  line in `src/text_detect.py` and `src/ocr_match.py`.
- Coarse visual sampling at 2 fps could miss a text flash under ~0.5s;
  tunable via `COARSE_SAMPLE_FPS`.
- No scene-cut detection yet to further shrink candidate windows on long
  videos.
- The `base` Whisper model is a speed/accuracy default; `--whisper-model
  small` or larger will improve transcription accuracy at the cost of
  runtime.
