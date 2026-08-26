# Dialogue Frame Finder

Given a video, finds the exact frame where a specific piece of dialogue
first appears - checking **both** on-screen text (OCR) and spoken audio
(ASR), run as independent parallel detectors and reconciled by a fusion
stage - and reports its timestamp, frame number, extracted text, a saved
image of that frame, and whether audio corroborates the visual match.

Both branches run **in parallel and stop as soon as either one confirms a
match**, instead of always scanning the entire video: whichever branch finds
the dialogue first signals the other to stop (see "How it decides when to
stop" below). The task requires a frame+image no matter which branch found
the dialogue, so if audio confirms the line was spoken but no matching
on-screen text is ever found for it, the frame at that exact spoken moment
is still extracted and saved as a best-effort answer - clearly marked
`match_source: "spoken_audio"` (vs. `"on_screen_text"`) rather than being
confused with an OCR-confirmed on-screen match.

See **`DESIGN.md`** for the full architecture/design rationale and
**`PROMPTS.md`** for LLM usage documentation.

## Getting started

Pick **one** of the two paths below — Docker (nothing to install except
Docker itself) or a local Python venv (needs Tesseract + ffmpeg installed on
your machine first). Both end up running the same pipeline.

### Option A — Docker (recommended, no OS-level setup)

Docker Desktop (or any Docker engine) is the only prerequisite - the image
bundles Tesseract, ffmpeg, and every Python dependency itself.

**1. Build the image** (from the repo root, once):

```bash
docker build -t dialogue-frame-finder .
```

**2. Run it.** If you already have the video downloaded locally (recommended
— no network dependency, and avoids re-downloading a large file every run):

```bash
docker run --rm \
  -v "/absolute/path/to/your_video.mp4:/data/video.mp4:ro" \
  -v "$(pwd)/output:/app/output" \
  dialogue-frame-finder \
  --video-path /data/video.mp4 \
  --text "My mind rebels at stagnation"
```

Or let it download the video by URL instead:

```bash
docker run --rm -v "$(pwd)/output:/app/output" dialogue-frame-finder \
  --url "https://ok.ru/video/248244667877" \
  --text "My mind rebels at stagnation"
```

Either way, `output/result.json` and `output/matched_frame.png` land in
`./output` on your host machine via the volume mount. The first run also
downloads the Whisper model for the audio branch (cached inside the
container layer after that — rebuild the image, or add a named volume at
`/root/.cache/huggingface`, to persist it across runs instead of
re-downloading every time).

`main.py`'s interactive prompt isn't wired into the Docker entrypoint (the
container always runs `src.pipeline` with CLI args) — use the CLI form
above inside Docker, or run `main.py` directly via the venv path below.

### Option B — local Python venv

**1. Install the OS-level binaries first** - Tesseract (OCR engine) and
ffmpeg (video/audio decoding). These are not pip packages, so `pip install`
alone won't get them:

```bash
# macOS
brew install tesseract ffmpeg

# Ubuntu / Debian
sudo apt-get update && sudo apt-get install -y tesseract-ocr ffmpeg

# Windows
# Tesseract: https://github.com/UB-Mannheim/tesseract/wiki (installer adds it to PATH)
# ffmpeg:    https://ffmpeg.org/download.html (add the extracted bin/ folder to PATH)
```

Verify both are on your `PATH` before continuing:

```bash
tesseract --version
ffmpeg -version
```

**2. Create a virtual environment and install the Python dependencies:**

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

**3. Run it** - see Usage below.

## Usage

### Interactive (`main.py`)

```bash
python main.py
```

Prompts for a video link and the target sentence, then runs the pipeline:

```
Video link: https://ok.ru/video/248244667877
Target sentence: My mind rebels at stagnation
```

### CLI (`src.pipeline`)

```bash
python -m src.pipeline \
  --video-path /path/to/your_video.mp4 \
  --text "My mind rebels at stagnation"

# Video-only, skip the audio/ASR branch entirely (faster)
python -m src.pipeline --video-path /path/to/video.mp4 --text "..." --skip-audio

# Larger Whisper model for better transcription accuracy (slower)
python -m src.pipeline --video-path /path/to/video.mp4 --text "..." --whisper-model small

# Print the full JSON result instead of the human-readable summary
python -m src.pipeline --video-path /path/to/video.mp4 --text "..." --json
```

`main.py` only accepts a video link (it always downloads via `--url`); use
the CLI form with `--video-path` if you already have the file locally and
want to skip re-downloading.

Either entry point prints a human-readable summary by default:

```
Timestamp : 00:05:24.989
Frame     : 7792
Text      : "My mind rebels at stagnation."
```

and always writes the full detail to `output/result.json` +
`output/matched_frame.png` regardless of which format is printed.

> **If a downloaded video seeks/decodes strangely** (e.g. ffmpeg logs
> `Missing reference picture` / `reference picture missing during reorder`
> during scanning): some sources hand back an MPEG-TS stream saved with a
> `.mp4` extension, which real MP4 tooling - and this pipeline's frame
> seeking - doesn't index correctly. Remux it once before use:
> `ffmpeg -i your_video.mp4 -c copy -movflags +faststart fixed_video.mp4`.
> `download_video()` does not currently do this automatically — see
> `DESIGN.md` for details.

## How it decides when to stop

The visual (OCR) coarse scan and the audio (ASR) branch run concurrently.
Whichever confirms a match first stops the other early rather than letting
it keep scanning to the end of the video:

- **Visual confirms first:** the coarse scan stops as soon as one of its
  candidate windows fine-scan-confirms a real match; the audio thread is a
  daemon thread bounded by `AUDIO_BRANCH_TIMEOUT_SEC` (45s) - it isn't
  force-killed, just no longer waited on once that budget is up, since audio
  is cross-check only and never required for the final answer.
- **Audio confirms first:** the coarse scan is signaled to stop, but — to
  avoid skipping an earlier on-screen occurrence the scan simply hasn't
  reached yet — it only actually stops once its own scan position has caught
  up to the audio-confirmed timestamp, not immediately on the signal. That
  window (and only that window) is then fine-scanned visually to look for a
  matching frame there.
- **Neither confirms anything ever:** the coarse scan runs to the end of the
  video (there's no way to conclude "not present" without looking
  everywhere), and the audio thread's timeout also elapses. Only in that
  case does the pipeline fall back to `full_video_fine_scan`.

If audio confirms a spoken match but the corresponding window has no
matching on-screen text, the frame at that exact spoken timestamp is
extracted anyway (see `extract_frame_at()` in `src/ocr_match.py`) and the
result is still `status: "found"`, but with `match_source: "spoken_audio"`
instead of `"on_screen_text"` — so it's never mistaken for an OCR-confirmed
match. `status: "not_found"` is reserved for when neither branch found
anything at all (or the audio-derived frame grab itself fails).

## Output

`result.json` + `matched_frame.png` written to `--output-dir`. Two real
shapes this can take:

**On-screen text match** (OCR-confirmed; audio available and
aligned/misaligned/absent):

```json
{
  "status": "found",
  "timestamp": "00:00:06.000",
  "frame_number": 150,
  "text": "My mind rebels at stagnation",
  "confidence": 100.0,
  "frame_image_path": "output/matched_frame.png",
  "match_source": "on_screen_text",
  "audio_confirmed": true,
  "confirming_audio_text": "my mind rebels at stagnation",
  "ambiguous_visual_candidates": [ ... ],
  "unconfirmed_audio_hits": [ ... ],
  "log": [ ... ]
}
```

`audio_confirmed`: `true` (spoken audio aligns with the matched frame),
`false` (visual match found but audio disagrees/is silent there), or `null`
(no audio data existed to check — no track, or the branch was skipped/failed).

**Spoken only, never shown on screen** (see "Validated run" below for how
this was actually produced, not a hypothetical — this is the real output
for the task's target video/phrase):

```json
{
  "status": "found",
  "timestamp": "00:05:24.989",
  "frame_number": 7792,
  "text": "My mind rebels at stagnation.",
  "confidence": 100.0,
  "frame_image_path": "output/matched_frame.png",
  "match_source": "spoken_audio",
  "audio_confirmed": true,
  "confirming_audio_text": "My mind rebels at stagnation.",
  "ambiguous_visual_candidates": [],
  "unconfirmed_audio_hits": [],
  "log": [ "...", "No on-screen text found for the spoken match at 325.00s - extracting that frame anyway as a best-effort answer (audio-derived, not OCR-confirmed)." ]
}
```

`match_source: "spoken_audio"` is the signal to look at when you need to
distinguish "the text was actually read off the screen" from "this is just
what was on screen when the line was spoken" — both are `status: "found"`
with a real frame+image, but only the first is an OCR-confirmed on-screen
match.

## Validated run

Run end-to-end against the actual target video/phrase from the task
(`https://ok.ru/video/248244667877`, `"My mind rebels at stagnation"`): the
audio branch found an exact spoken match (`score: 100`) at **00:05:25**, the
visual branch confirmed there is no matching on-screen text anywhere in the
video, and the pipeline extracted and saved the frame at that spoken moment
(`frame_number: 7792`) with `match_source: "spoken_audio"` — a real,
verified result for this specific video/phrase pair, not an untested guess.
See `DESIGN.md` for the fuller validation notes, including an unrelated
container/decoding issue found in the downloaded source file (worked around
by remuxing — see the note under Usage above) and a couple of bugs caught
and fixed during that validation (a garbled-OCR false positive, and an audio
fuzzy-match false positive on an unrelated sentence).

## Project layout

```
main.py           # interactive entry point: prompts for a video link + target sentence
src/
  pipeline.py     # orchestrator: runs video + audio branches in parallel, stops on first confirmation, then fuses
  text_detect.py  # visual branch, stage 1: coarse sparse-sampled OCR scan (interruptible by the audio branch)
  ocr_match.py    # visual branch, stage 2: frame-accurate OCR + fuzzy match
  audio_match.py  # audio branch: faster-whisper transcription + fuzzy match, stops at its first match
  fuse.py         # reconciles visual + audio branches into one answer
  av_probe.py     # checks whether a video has an audio track
  download.py     # yt-dlp video download (only used if no local file is given)
  config.py       # tunable thresholds
  utils.py        # timestamp/text helpers
Dockerfile        # reproducible environment (tesseract + ffmpeg + audio/video deps)
DESIGN.md         # design document (required write-up)
PROMPTS.md        # LLM prompt documentation (required write-up)
output/           # pipeline outputs from the most recent run
```
