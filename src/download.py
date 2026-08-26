import os
import yt_dlp

def download_video(url: str, out_dir: str = "sample_media", filename: str = "source") -> str:
    os.makedirs(out_dir, exist_ok=True)
    out_template = os.path.join(out_dir, f"{filename}.%(ext)s")

    ydl_opts = {
        "outtmpl": out_template,
        "format": "best[ext=mp4]/bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "quiet": False,
        "noprogress": False
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info is None:
            raise ValueError(f"yt-dlp could not extract any info for URL: {url}")
        final_path = ydl.prepare_filename(info)
        if not final_path.endswith(".mp4") and os.path.exists(os.path.splitext(final_path)[0] + ".mp4"):
            final_path = os.path.splitext(final_path)[0] + ".mp4"

    if not os.path.exists(final_path):
        raise FileNotFoundError(f"yt-dlp reported success but output file not found at {final_path}")
    return final_path


if __name__ == "__main__":
    import sys

    url = sys.argv[1]
    path = download_video(url)
    print(f"Downloaded to: {path}")