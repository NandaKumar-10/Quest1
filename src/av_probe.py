import json
import subprocess


def has_audio_stream(video_path: str) -> bool:
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "a",
                "-show_entries", "stream=index",
                "-of", "json",
                video_path,
            ],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(result.stdout or "{}")
        return bool(data.get("streams"))
    except Exception:
        return True