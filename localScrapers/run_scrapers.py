#!/usr/bin/env python3
"""
Run localScrapers outlets in date-range or incremental (real-time) mode.

Examples:
  python run_scrapers.py --incremental
  python run_scrapers.py --incremental ftlk dailynews
  python run_scrapers.py 2026-06-01 2026-06-12
  SCRAPE_MODE=incremental python run_scrapers.py
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
from datetime import datetime, timedelta

SCRAPERS_DIR = os.path.dirname(os.path.abspath(__file__))
ALL_SCRAPERS = [
    "ceylontoday_selenium_json",
    "dailynews_selenium_json",
    "dinamina_selenium_json",
    "economynext_selenium_json",
    "ftlk_selenium_json",
    "island_selenium_json",
    "sundayobserver_selenium_json",
    "thinakaran_selenium_json",
]

ALIASES = {
    "ceylontoday": "ceylontoday_selenium_json",
    "dailynews": "dailynews_selenium_json",
    "dinamina": "dinamina_selenium_json",
    "economynext": "economynext_selenium_json",
    "ftlk": "ftlk_selenium_json",
    "ft": "ftlk_selenium_json",
    "island": "island_selenium_json",
    "sundayobserver": "sundayobserver_selenium_json",
    "observer": "sundayobserver_selenium_json",
    "thinakaran": "thinakaran_selenium_json",
}


def _resolve(name: str) -> str:
    key = name.lower().replace(".py", "")
    if key in ALIASES:
        return ALIASES[key]
    if key.endswith("_selenium_json"):
        return key
    return key


def main() -> int:
    args = sys.argv[1:]
    incremental = "--incremental" in args or os.getenv("SCRAPE_MODE", "").lower() == "incremental"
    args = [a for a in args if a != "--incremental"]

    targets = [_resolve(a) for a in args] if args else list(ALL_SCRAPERS)
    unknown = [t for t in targets if t not in ALL_SCRAPERS]
    if unknown:
        print(f"[ERROR] Unknown scraper(s): {', '.join(unknown)}")
        print(f"Available: {', '.join(ALL_SCRAPERS)}")
        return 1

    if incremental:
        sys.path.insert(0, SCRAPERS_DIR)
        from incremental_outlets import INCREMENTAL_BY_MODULE

        total = 0
        for name in targets:
            print(f"\n{'#' * 60}\n# INCREMENTAL: {name}\n{'#' * 60}")
            fn = INCREMENTAL_BY_MODULE.get(name)
            if not fn:
                print(f"[ERROR] No incremental runner for {name}")
                continue
            try:
                total += fn()
            except Exception as e:
                print(f"[ERROR] {name} failed: {e}")
        print(f"\n[DONE] Incremental run complete — {total} new articles total")
        return 0

    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=1)
    if len(args) >= 2:
        try:
            start_date = datetime.strptime(args[0], "%Y-%m-%d").date()
            end_date = datetime.strptime(args[1], "%Y-%m-%d").date()
            targets = [_resolve(a) for a in args[2:]] if len(args) > 2 else list(ALL_SCRAPERS)
        except ValueError as e:
            print(f"[ERROR] Invalid dates: {e}")
            return 1

    for name in targets:
        script = os.path.join(SCRAPERS_DIR, f"{name}.py")
        print(f"\n{'#' * 60}\n# DATE RANGE: {name} ({start_date} → {end_date})\n{'#' * 60}")
        subprocess.run(
            [sys.executable, script, start_date.isoformat(), end_date.isoformat()],
            cwd=SCRAPERS_DIR,
            check=False,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
