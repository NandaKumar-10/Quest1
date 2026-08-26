# Reproducible environment for the dialogue-frame-finder pipeline.
# Bundles Tesseract OCR + ffmpeg (OS-level binaries, not pip-installable)
# alongside all Python dependencies, including the audio/ASR branch
# (faster-whisper), so the full dual-modality pipeline runs out of the box.

FROM python:3.12-slim

# tesseract-ocr    : OCR engine for the visual branch (pytesseract wraps this binary)
# ffmpeg           : audio/video demuxing for yt-dlp, opencv, and faster-whisper
# libgl1           : opencv-python-headless needs libGL at import time on slim images
# libglib2.0-0     : opencv-python-headless also needs this on slim images
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (separate layer) so code edits don't bust the
# dependency cache on rebuild. This layer is the slow one (~1-2 min) due to
# faster-whisper's ctranslate2 wheel - it only reruns if requirements.txt changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/

RUN mkdir -p /app/output /app/sample_media

ENTRYPOINT ["python", "-m", "src.pipeline"]
# Example (local video file, preferred - see README for why):
#   docker run --rm \
#     -v "$(pwd)/my_video.mp4:/data/video.mp4:ro" \
#     -v "$(pwd)/output:/app/output" \
#     dialogue-frame-finder \
#     --video-path /data/video.mp4 --text "My mind rebels at stagnation"
CMD ["--help"]
