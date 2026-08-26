# LLM Usage Documentation

This solution was built with Claude (Anthropic) as a coding assistant. In the
interest of the "did you write it yourself vs. can you engineer with AI"
evaluation criterion, this file documents what was asked of the LLM and how
the output was used.

## Prompt 1 — architecture / approach discussion (no code yet)

> "How to approach this problem? Should I need to consider the frames of the
> video or audio or both?"

**How the response was used:** this was a design discussion, not code
generation. It established the core architectural decision the rest of the
solution follows: on-screen dialogue is a visual/OCR problem by definition
(the output requires the exact frame image), and audio/ASR can only ever be
a secondary, optional heuristic to narrow the search space — never the
source of truth. This reasoning is preserved and expanded in `DESIGN.md`
section 2.

## Prompt 2 — code generation

> "Help me to generate the code for this task"

**How the response was used:** the LLM proposed and generated the concrete
pipeline structure (download → optional audio hint → coarse scan → fine
scan → decision), split into separate modules per stage
(`download.py`, `audio_hint.py`, `text_detect.py`, `ocr_match.py`,
`pipeline.py`). The LLM also:
- selected the OCR engine (Tesseract, given it was already available and
  needs no model download) and the fuzzy-matching library (RapidFuzz),
- chose the two-threshold (loose coarse / strict fine) matching design,
- wrote and ran a synthetic-video validation test (ground truth: target
  phrase rendered at a known frame) to prove the pipeline works end-to-end,
  since the actual target URL (`ok.ru`) was not reachable from the
  development sandbox's restricted network.

**What was reviewed/would need review before treating this as final:** the
default thresholds (`COARSE_FUZZY_THRESHOLD=55`, `FINE_FUZZY_THRESHOLD=80`,
`COARSE_SAMPLE_FPS=2`) are reasonable starting points but were not tuned
against real, noisy footage (only against a clean synthetic test) — see
`DESIGN.md` section 10 for the acknowledged limitations and next steps.

## Prompt 3 — packaging / environment question

> "Is it not possible to install tesseract-ocr in my venv?" followed by
> "Or shall I go with docker?"

**How the response was used:** this was a follow-up environment/tooling
discussion, not new pipeline logic. It surfaced that Tesseract is an
OS-level binary, not a pip package, and that a venv alone can't contain it.
The LLM proposed Docker as the cleanest fix and generated `Dockerfile` and
`.dockerignore`, which were added to the repo (not tested with an actual
`docker build`, since Docker itself isn't available in the sandbox used to
build this — this should be verified once you have Docker locally, before
relying on it in the interview).

## Prompt 4 — architecture redesign

> "I think, I need to look for both image and audio, I need you help to
> figure out the architecture diagram for this one"

**How the response was used:** this changed audio from a search-narrowing
hint (used and then discarded) into a first-class parallel detector whose
result persists into a dedicated fusion stage. The LLM proposed the
video/audio-branches-in-parallel-then-fuse architecture and produced an
architecture diagram, which is preserved in `DESIGN.md` section 2.

## Prompt 5 — full rewrite, Docker-runnable, local-file-first, generalized

> "Yes, I need you to Give complete code from starting and make it runnable
> using docker, and downloading the video again is not feasible, Already i
> have downloaded video, so that i can use for testing alone, And this
> system should be capable of work even if the video and audio is changed"

**How the response was used:** this drove three concrete changes, all LLM-
authored then reviewed:
1. `--video-path` is now the primary, documented entry point (download is
   opt-in, only triggered if no local path is given) — see
   `_resolve_video_path()` in `src/pipeline.py`.
2. `src/audio_match.py` (replacing the earlier `audio_hint.py`) and
   `src/fuse.py` implement the audio branch as an independent detector and
   its reconciliation with the visual branch, per the new architecture.
3. Generality was addressed by removing every runtime assumption that was
   specific to the reference video: fps/frame-count/audio-track-presence are
   all probed, not assumed; the audio branch fails closed (caught
   exceptions, logged, pipeline continues) rather than crashing on an
   unfamiliar codec or missing model; thresholds are fuzzy floors rather
   than exact-match gates. See `DESIGN.md` section 9 for the full list.

**What was reviewed/tested before accepting this as final:** the LLM ran two
end-to-end tests against synthetic ground-truth videos it generated itself
(see `DESIGN.md` section 10) — one without audio, one with a synthesized
spoken track. Both correctly located frame 150. The audio branch's
transcription step could not be fully exercised in the sandbox
(`huggingface.co`, where `faster-whisper` fetches model weights, isn't
network-reachable there), which the LLM disclosed rather than silently
omitting; the graceful-degradation path this exposed (transcription failure
caught → logged → video branch still succeeds) was itself treated as a
successful test of the "works even when audio isn't available" requirement.
This is a concrete example of the LLM being transparent about what it could
and couldn't verify in its own environment, and of the human review needed
before treating auto-generated fusion/ambiguity logic as final — for
instance, the first draft of the ambiguity-clustering logic in `fuse.py`
incorrectly flagged persistent on-screen text (many consecutive matching
frames) as "ambiguous"; this was caught by inspecting the actual test output
and fixed by clustering matches by frame proximity before flagging.

## Prompt 6 — real target video, decoder warnings

> "python -m src.pipeline --video-path downloaded_video.mp4 --text 'My mind
> rebels at stagnation' ... [h264] Missing reference picture, default is 2 /
> reference picture missing during reorder ... What to do? And how to
> resolve this one"

**How the response was used:** this was the first run against the actual
task video (downloaded from the real `ok.ru` URL, not a synthetic clip).
Diagnosis: `ffprobe` showed the file was actually raw MPEG-TS content saved
with a `.mp4` extension (`ts_packetsize=188`, `is_avc=false`, no seek index),
and a full-file `ffmpeg -v warning` decode pass found ~605 genuinely
corrupt/missing packets spread evenly across the whole ~54-minute file —
real data loss from the original download, not something remuxing can
recover. The file was remuxed (`ffmpeg -c copy -movflags +faststart`) to
restore a working seek index for testing. **Not yet fixed in code:**
`download_video()` itself doesn't do this remux step, so the same issue
would resurface for anyone running `--url` directly against a source that
serves TS-in-MP4 — flagged in `DESIGN.md` as a known gap rather than
silently left undocumented.

## Prompt 7 — implementation review against the actual problem statement

> User attached the assignment PDF and the real target video, asked whether
> the existing implementation was correct and needed changes.

**How the response was used:** a full code review against every requirement
in the brief (exact frame, timestamp, frame number, text, image; works
without manual inspection; robust to quality/resolution/fps variation;
explains where-to-look / relevant-frame / text-extraction / ambiguity
handling). Found the architecture sound, but surfaced two concrete gaps: (1)
the container-normalization gap from Prompt 6 isn't handled by
`download_video()`, and (2) the coarse scan's per-sample `cv2.VideoCapture`
seeking (~9,800 seeks across a 54-minute video at the original
`COARSE_SAMPLE_FPS=3`) was why the very first full run took over an hour of
CPU time — motivating Prompt 8.

## Prompt 8 — "stop once either branch finds it, don't run fully"

> "I think, we can stop if anyone like audio or in video finds the target
> word rather than running it fully" ... later: "I know the text is not
> appearing in the video anywhere. Only it is been spoken. That's what I'm
> saying."

**How the response was used:** this drove the early-stop redesign in
`src/pipeline.py`, `src/text_detect.py` (`coarse_scan` became the
interruptible generator `coarse_scan_iter`), and `src/audio_match.py`
(Whisper segments consumed lazily, stopping at the first match instead of
transcribing/scoring the whole track). **Two real bugs were introduced and
then caught and fixed during this same exchange, not glossed over:**
1. The first version of the early-stop let a garbled OCR read of a corrupted
   frame (`"a . ; = <_<..."`, from one of the corrupt packets found in
   Prompt 6) fuzzy-match the target at a deceptively high score via
   `rapidfuzz.fuzz.partial_ratio`'s bias toward short strings, causing a
   false "found" result. Fixed with a minimum-OCR-length gate
   (`MIN_OCR_LEN_RATIO` in `config.py`) applied to both scan stages.
2. Once the audio branch could stop the video branch, an unrelated Whisper
   segment ("Yes, well, I'm not surprised... pouring rain... a cab") scored
   50/100 against the target via `partial_ratio` (the same short-string bias,
   this time on a long unrelated sentence) and triggered a false early stop.
   Fixed by switching the audio branch to `fuzz.token_set_ratio` (verified:
   ~30 for the unrelated sentence vs. 90-100 for real matches) and retuning
   `AUDIO_FUZZY_THRESHOLD` accordingly.
3. Separately, the pipeline was also found to be blocking on the *entire*
   Whisper transcription via `ThreadPoolExecutor`'s implicit join even after
   the visual branch had already confirmed its answer — switched the audio
   branch to a daemon `threading.Thread` with a bounded `.join(timeout=...)`
   (`AUDIO_BRANCH_TIMEOUT_SEC`) so a spoken-only-never-found audio search
   can't block the process indefinitely.

With these fixes, the pipeline correctly found the real, verified answer for
this video/phrase: spoken (not on-screen) at 00:05:25, `score: 100`.

## Prompt 9 — generalization check surfaces a correctness bug

> "Will it work for other video with different target sentence?"

**How the response was used:** rather than just asserting "yes," tracing the
Prompt 8 early-stop logic against a hypothetical different video (dialogue
shown on-screen early *and* spoken later) surfaced a real remaining bug: the
coarse scan could abort the instant the audio branch signaled a match,
regardless of how far the visual scan itself had actually progressed —
skipping straight past an earlier on-screen occurrence it hadn't reached
yet. Fixed by adding `stop_not_before` to `coarse_scan_iter`: the scan now
only honors the audio-branch stop signal once its own timeline position has
already reached the audio-confirmed timestamp, so it can never skip
territory it hasn't covered.

## Prompt 10 — single interactive entry point

> "I need one change in this... I need to run a single python file like
> python main.py then it should prompt to ask the video link and prompt to
> ask the what is the target sentence then start to search"

**How the response was used:** added `main.py` at the repo root — a thin
wrapper that prompts for a video link and target sentence, then calls the
same `run_pipeline()` the CLI (`src.pipeline`) uses, with the same
JSON-result/exit-code contract.