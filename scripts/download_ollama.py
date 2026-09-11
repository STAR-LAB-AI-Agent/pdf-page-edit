"""Resume the pinned official Ollama release in verified byte ranges."""
import concurrent.futures
import hashlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "runtime/ollama-windows-amd64.zip"
SIZE = 1469175900
SHA256 = "52cb36a62e7e501f61514f60212dec7117b6c098811357585e02fffe32d2fcd7"
URL = "https://api.github.com/repos/ollama/ollama/releases/assets/543087793"


def download(item):
    start, end, part = item
    if not part.exists() or part.stat().st_size != end-start+1:
        subprocess.run(["curl.exe", "-fsSL", "--retry", "3", "--connect-timeout", "20",
                        "--max-time", "300", "-H", "Accept: application/octet-stream",
                        "--range", f"{start}-{end}", URL + f"?range_start={start}", "-o", str(part)], check=True)
    if part.stat().st_size != end-start+1:
        raise ValueError("Server did not honor requested byte range")
    print(f"Range complete: {start}-{end}", flush=True)
    return part


def main():
    TARGET.parent.mkdir(exist_ok=True)
    parts_dir = TARGET.parent / "download-parts"
    parts_dir.mkdir(exist_ok=True)
    offset = TARGET.stat().st_size if TARGET.exists() else 0
    if offset > SIZE:
        raise ValueError("Existing file is larger than official artifact")
    items = [(start, min(start + 32*1024*1024, SIZE)-1, parts_dir / f"{start}.part")
             for start in range(offset, SIZE, 32*1024*1024)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        list(executor.map(download, items))
    with TARGET.open("ab") as output:
        for _, _, part in items:
            with part.open("rb") as source:
                for chunk in iter(lambda: source.read(1024*1024), b""):
                    output.write(chunk)
    digest = hashlib.sha256()
    with TARGET.open("rb") as source:
        for chunk in iter(lambda: source.read(1024*1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != SHA256:
        raise ValueError("Official SHA256 check failed; do not extract")
    print("Official SHA256 verified", flush=True)


if __name__ == "__main__":
    main()
