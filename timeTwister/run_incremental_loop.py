"""
Run incremental scrapers on an interval (default: every 10 minutes).

Each scraper stops when it sees an article URL already in data/*_latest_news.json
from the previous run.

Usage:
  python run_incremental_loop.py
  python run_incremental_loop.py --interval 600
  python run_incremental_loop.py --once          # single pass, then exit
  python run_incremental_loop.py --only aruna
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

from scraper_registry import SCRAPERS

INCREMENTAL_SCRAPERS = {
    s.id: s.module for s in SCRAPERS if s.incremental
}


def run_once(only: str | None) -> bool:
    targets = INCREMENTAL_SCRAPERS.items()
    if only:
        key = only.lower()
        if key not in INCREMENTAL_SCRAPERS:
            print(f"[ERROR] Unknown scraper '{only}'. Supported: {', '.join(INCREMENTAL_SCRAPERS)}")
            return False
        targets = [(key, INCREMENTAL_SCRAPERS[key])]

    ok = True
    for name, module in targets:
        script = ROOT / "scrapers" / f"{module}.py"
        print(f"\n{'=' * 60}\n[INCREMENTAL] {name}\n{'=' * 60}")
        result = subprocess.run(
            [sys.executable, "-u", str(script), "--incremental"],
            cwd=str(ROOT),
            check=False,
        )
        if result.returncode != 0:
            ok = False
            print(f"[ERROR] {name} exited with code {result.returncode}")
    return ok


def main():
    parser = argparse.ArgumentParser(description="Incremental news scraper loop")
    parser.add_argument(
        "--interval",
        type=int,
        default=int(os.getenv("SCRAPE_INTERVAL_SECONDS", "600")),
        help="Seconds between runs (default: 600 = 10 minutes)",
    )
    parser.add_argument("--once", action="store_true", help="Run one pass and exit")
    parser.add_argument("--only", type=str, help=f"Run one outlet: {', '.join(INCREMENTAL_SCRAPERS)}")
    args = parser.parse_args()

    if args.once:
        sys.exit(0 if run_once(args.only) else 1)

    print(f"[LOOP] Incremental scrape every {args.interval}s. Ctrl+C to stop.")
    while True:
        run_once(args.only)
        print(f"\n[LOOP] Sleeping {args.interval}s until next run...")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
