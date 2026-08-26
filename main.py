import utils.transport
import utils.session
import sys

from src.pipeline import format_result, run_pipeline

def main():
    url = input("Video link: ").strip()
    target_text = input("Target sentence: ").strip()

    if not url:
        print("A video link is required.")
        sys.exit(1)
    if not target_text:
        print("A target sentence is required.")
        sys.exit(1)

    result = run_pipeline(url=url, target_text=target_text)

    print(format_result(result))
    if result["status"] != "found":
        sys.exit(1)

if __name__ == "__main__":
    main()