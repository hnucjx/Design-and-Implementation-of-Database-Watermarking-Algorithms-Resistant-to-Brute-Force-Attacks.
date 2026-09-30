"""Real-network download acceptance for the YouTube downloader.

Runs the actual FastAPI app (real ``YtDlpService``, real yt-dlp, real network)
against a public playlist and measures, for each concurrency level:

  * per-item bytes / elapsed / average speed / failure reason
  * aggregate wall time, total bytes and aggregate throughput
  * how many items stayed ``running`` longer than ``--stall-hint``

Designed for the acceptance criteria in PLAN.md §6.5:
  5-concurrency wall <= 45% of 1-concurrency wall, 0 failures, no item running > 5 min.

Usage::

    python scripts/acceptance_real.py --tmp C:/tmp/acc --items 6 --concurrency 1,3,5
    python scripts/acceptance_real.py --throttle-rate 64          # A/B against old default
    python scripts/acceptance_real.py --resolution 360p --items 2 # quick smoke

Notes:
  * ``--proxy`` defaults to the Windows system proxy (wininet), which is what the
    browser uses. Without it yt-dlp connects directly and every request times out
    on machines where direct egress is blocked.
  * Each concurrency level gets a fresh SQLite database and download directory so
    ``skip_existing`` never short-circuits a round.
"""
from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))


def detect_system_proxy() -> str | None:
    """Return the WinINet proxy server (host:port) if the OS proxy is enabled."""
    if sys.platform != "win32":
        return None
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        )
        enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
        server, _ = winreg.QueryValueEx(key, "ProxyServer")
    except OSError:
        return None
    if not enabled or not server:
        return None
    # WinINet may report per-scheme entries like "http=127.0.0.1:7890;https=...".
    first = str(server).split(";")[0]
    if "=" in first:
        first = first.split("=", 1)[1]
    return first or None


def apply_proxy(proxy: str | None) -> None:
    if not proxy:
        return
    if "://" not in proxy:
        proxy = f"http://{proxy}"
    os.environ["HTTP_PROXY"] = proxy
    os.environ["HTTPS_PROXY"] = proxy
    os.environ["http_proxy"] = proxy
    os.environ["https_proxy"] = proxy
    print(f"proxy: {proxy}")


def run_round(
    *,
    url: str,
    items: list[int],
    concurrency: int,
    resolution: str,
    tmp: Path,
    mode: str,
    subtitles: str,
    throttle_rate: int | None,
    stall_timeout: float,
    aria2c: bool,
    aria2c_connections: int,
    poll_timeout: float,
) -> dict:
    from fastapi.testclient import TestClient

    from app.config import AppSettings
    from app.main import create_app

    settings_kwargs = dict(
        data_dir=tmp / "data",
        download_dir=tmp / "downloads",
        database_path=tmp / "data" / f"acc-{concurrency}.sqlite3",
        default_concurrency=concurrency,
        default_resolution=resolution,
        stall_timeout_seconds=stall_timeout,
        aria2c_enabled=aria2c,
        aria2c_connections=aria2c_connections,
    )
    if throttle_rate is not None:
        settings_kwargs["throttled_rate_kbps"] = throttle_rate
    settings = AppSettings(**settings_kwargs)

    started_at = time.time()
    with TestClient(create_app(settings=settings)) as client:
        response = client.post(
            "/api/jobs",
            json={
                "url": url,
                "options": {
                    "mode": mode,
                    "resolution": resolution,
                    "playlist_items": items,
                    "skip_existing": False,
                    "subtitle_languages": [] if subtitles == "all" else [s for s in subtitles.split(",") if s],
                },
            },
        )
        if response.status_code != 201:
            raise SystemExit(f"create job failed ({response.status_code}): {response.text[:400]}")
        job_id = response.json()["id"]

        deadline = time.perf_counter() + poll_timeout
        detail = {}
        while time.perf_counter() < deadline:
            detail = client.get(f"/api/jobs/{job_id}").json()
            if detail.get("status") in {"succeeded", "failed", "cancelled"}:
                break
            time.sleep(0.5)
        wall = time.time() - started_at

    rows = read_items(settings.database_path, job_id)
    return {"concurrency": concurrency, "wall": wall, "status": detail.get("status"), "job": detail, "rows": rows}


def read_items(db_path: Path, job_id: str) -> list[dict]:
    if not db_path.exists():
        return []
    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute(
            'SELECT title, "index", status, downloaded_bytes, total_bytes, speed, error, '
            "started_at, finished_at FROM jobitem WHERE job_id = ? ORDER BY \"index\"",
            (job_id,),
        )
        rows = [dict(r) for r in cur.fetchall()]
    finally:
        con.close()

    for row in rows:
        row["elapsed"] = _seconds_between(row.get("started_at"), row.get("finished_at"))
    return rows


def _seconds_between(started: str | None, finished: str | None) -> float:
    if not started or not finished:
        return 0.0
    from datetime import datetime

    def parse(value: str) -> datetime:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)

    try:
        return max(0.0, (parse(finished) - parse(started)).total_seconds())
    except ValueError:
        return 0.0


def summarize(result: dict) -> dict:
    rows = result["rows"]
    total_bytes = sum(r["downloaded_bytes"] or 0 for r in rows)
    failed = [r for r in rows if r["status"] == "failed"]
    slow = [r for r in rows if (r["elapsed"] or 0) > 300]
    speeds = [r["speed"] for r in rows if r["speed"]]
    return {
        "concurrency": result["concurrency"],
        "status": result["status"],
        "wall_s": round(result["wall"], 2),
        "items": len(rows),
        "failed": len(failed),
        "bytes": total_bytes,
        "aggregate_kib_s": round(total_bytes / 1024 / result["wall"], 1) if result["wall"] else 0,
        "mean_item_speed_kib_s": round(sum(speeds) / len(speeds) / 1024, 1) if speeds else 0,
        "items_over_5min": len(slow),
        "errors": sorted({(r["error"] or "")[:60] for r in failed}),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="https://www.youtube.com/@3blue1brown/videos")
    parser.add_argument("--items", default="1-6", help="playlist item indices, e.g. 1-6 or 1,2,3")
    parser.add_argument("--concurrency", default="1,3,5")
    parser.add_argument("--resolution", default="480p")
    parser.add_argument(
        "--mode",
        default="video_only",
        choices=["video_only", "video_subtitles", "subtitles_only"],
        help="video_only isolates media throughput; video_subtitles with --subtitles=all "
        "triggers YouTube HTTP 429 and fails the whole item",
    )
    parser.add_argument("--subtitles", default="en", help="comma separated languages, or 'all'")
    parser.add_argument("--tmp", default=str(REPO_ROOT / "tmp_acceptance"))
    parser.add_argument("--throttle-rate", type=int, default=None, help="A/B: force throttled_rate_kbps")
    parser.add_argument("--stall-timeout", type=float, default=90.0)
    parser.add_argument("--aria2c", action="store_true")
    parser.add_argument("--aria2c-connections", type=int, default=2)
    parser.add_argument("--poll-timeout", type=float, default=2400.0)
    parser.add_argument("--proxy", default=None, help="default: Windows system proxy")
    parser.add_argument("--csv", default=str(REPO_ROOT / "tmp_acceptance" / "acceptance.csv"))
    args = parser.parse_args(argv)

    apply_proxy(args.proxy or detect_system_proxy())

    if "-" in args.items:
        lo, hi = args.items.split("-", 1)
        items = list(range(int(lo), int(hi) + 1))
    else:
        items = [int(x) for x in args.items.split(",") if x.strip()]

    concurrency_levels = [int(c) for c in args.concurrency.split(",") if c.strip()]
    tmp_root = Path(args.tmp)
    tmp_root.mkdir(parents=True, exist_ok=True)

    summaries = []
    all_rows = []
    for concurrency in concurrency_levels:
        tmp = tmp_root / f"c{concurrency}"
        print(f"\n=== concurrency={concurrency} resolution={args.resolution} items={items} ===", flush=True)
        result = run_round(
            url=args.url,
            items=items,
            concurrency=concurrency,
            resolution=args.resolution,
            tmp=tmp,
            mode=args.mode,
            subtitles=args.subtitles,
            throttle_rate=args.throttle_rate,
            stall_timeout=args.stall_timeout,
            aria2c=args.aria2c,
            aria2c_connections=args.aria2c_connections,
            poll_timeout=args.poll_timeout,
        )
        summary = summarize(result)
        summaries.append(summary)
        for r in result["rows"]:
            all_rows.append({"concurrency": concurrency, **r})
        print(summary, flush=True)
        for r in result["rows"]:
            print(
                f"  {r['status']:9s} {(r['downloaded_bytes'] or 0)/1048576:7.2f} MiB "
                f"{r['elapsed'] or 0:7.1f}s {(r['speed'] or 0)/1024:8.1f} KiB/s  {r['title'][:48]}"
                + (f"  ERR={r['error'][:70]}" if r["error"] else ""),
                flush=True,
            )

    csv_path = Path(args.csv)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["concurrency", "title", "status", "downloaded_bytes", "total_bytes", "elapsed", "speed", "error"])
        writer.writeheader()
        for row in all_rows:
            writer.writerow({k: row.get(k) for k in writer.fieldnames})
    print(f"\nCSV: {csv_path}")

    summary_path = csv_path.with_name("acceptance-summary.csv")
    with summary_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(summaries[0].keys()) if summaries else ["concurrency"])
        writer.writeheader()
        for s in summaries:
            writer.writerow({**s, "errors": " | ".join(s["errors"])})
    print(f"summary CSV: {summary_path}")
    for s in summaries:
        print("wall", s)

    by_c = {s["concurrency"]: s for s in summaries}
    print("\n=== verdict ===")
    if 1 in by_c and 5 in by_c:
        ratio = by_c[5]["wall_s"] / by_c[1]["wall_s"] if by_c[1]["wall_s"] else float("inf")
        ok = ratio <= 0.45
        print(f"5-conc wall / 1-conc wall = {ratio:.2%} (threshold 45%) -> {'PASS' if ok else 'FAIL'}")
    for s in summaries:
        print(f"  c={s['concurrency']}: failed={s['failed']} over5min={s['items_over_5min']} bytes={s['bytes']}")
        if s["failed"] == 0 and s["items_over_5min"] == 0:
            print("    stability PASS")
        else:
            print("    stability FAIL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
