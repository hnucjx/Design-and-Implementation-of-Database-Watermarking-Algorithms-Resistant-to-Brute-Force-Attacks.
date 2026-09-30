"""Show whether `throttledratelimit` puts yt-dlp into an unbounded re-extract loop.

Usage:  python scripts/bench_throttle_guard.py [temp_dir] [throttled_rate_kbps]

A local HTTP server serves a file at ~10 KB/s. With the guard on (64) yt-dlp
aborts and re-extracts every ~5s forever; with it off (0) the download proceeds
in a single continuous request. The script prints the HTTP GET count, whether
the download thread was still running after 25s, and the byte-progress samples.
"""
import http.server
import socketserver
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import yt_dlp  # noqa: E402

PAYLOAD = b"\x00" * (512 * 1024)
CHUNK = 10 * 1024
SLEEP_PER_CHUNK = 1.0
requests = []


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        requests.append((time.perf_counter(), self.path))
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(PAYLOAD)))
        self.end_headers()
        try:
            for offset in range(0, len(PAYLOAD), CHUNK):
                self.wfile.write(PAYLOAD[offset:offset + CHUNK])
                self.wfile.flush()
                time.sleep(SLEEP_PER_CHUNK)
        except (BrokenPipeError, ConnectionResetError):
            return

    def log_message(self, *args):
        return


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> None:
    server = Server(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/slow.mp4"

    opts = {
        "quiet": True,
        "no_warnings": True,
        "ignoreconfig": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 2,
        "fragment_retries": 2,
        "continuedl": True,
        "throttledratelimit": int(sys.argv[2]) * 1024 if len(sys.argv) > 2 else 64 * 1024,
        "outtmpl": str(Path(sys.argv[1] if len(sys.argv) > 1 else ".") / "probe-%(title)s.%(ext)s"),
    }

    result = {}
    samples: list[tuple[float, int]] = []

    def hook(payload):
        if payload.get("status") == "downloading" and payload.get("downloaded_bytes"):
            samples.append((time.perf_counter(), int(payload["downloaded_bytes"])))

    opts["progress_hooks"] = [hook]

    def run():
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
        except Exception as exc:  # noqa: BLE001
            result["error"] = f"{type(exc).__name__}: {exc}"

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout=25)
    alive = thread.is_alive()
    server.shutdown()
    print(f"capped at 25s; download thread still running: {alive}")
    print(f"HTTP GET count: {len(requests)}")
    for index, (stamp, path) in enumerate(requests[:12], 1):
        print(f"  #{index} +{stamp - requests[0][0]:.2f}s {path}")
    print("error:", result.get("error"))
    print(f"progress samples: {len(samples)}")
    base = samples[0][0] if samples else 0.0
    for stamp, done in samples[:20]:
        print(f"  +{stamp - base:5.2f}s downloaded={done}")


if __name__ == "__main__":
    main()
