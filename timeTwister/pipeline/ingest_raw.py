"""
Upsert scraped JSON batches into Supabase raw_articles.

Incremental scrapers replace JSON with only the latest batch — run this after
every scrape (GHA and local) so rows accumulate in the database.

Examples:
  python pipeline/ingest_raw.py --gha-only
  python pipeline/ingest_raw.py --local-only
  python pipeline/ingest_raw.py --outlets dailymirror virakesari
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

APP_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = APP_DIR / "data"
SCRAPERS_DIR = APP_DIR / "scrapers"

sys.path.insert(0, str(SCRAPERS_DIR))
sys.path.insert(0, str(APP_DIR))

from incremental import normalize_link  # noqa: E402
from scraper_registry import (  # noqa: E402
    GHA_SCRAPER_IDS,
    LOCAL_SCRAPER_IDS,
    SCRAPER_BY_ID,
    SCRAPERS,
)

SKIP_NAME_PARTS = (
    "_checkpoint",
    "_translated_en",
    "_archive",
    "discarded_links",
)

FILE_TO_OUTLET: dict[str, str] = {s.data_file: s.id for s in SCRAPERS}
OUTLET_DATA_FILES: dict[str, str] = {s.id: s.data_file for s in SCRAPERS}


def _parse_published_at(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(text.split("+")[0].split(".")[0], fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            continue
    return None


def _content_hash(title: str, summary: str) -> str:
    payload = f"{title}\n{summary}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _should_skip_file(path: Path) -> bool:
    name = path.name
    if not name.endswith(".json"):
        return True
    return any(part in name for part in SKIP_NAME_PARTS)


def _article_row(article: dict[str, Any], outlet_id: str, run_id: str | None) -> dict[str, Any] | None:
    url = normalize_link(article.get("link") or "")
    if not url:
        return None

    title = (article.get("title") or "").strip() or "(no title)"
    summary = (article.get("summary") or "").strip()
    description = (article.get("description") or "").strip() or None
    if not summary and description:
        summary = description

    row: dict[str, Any] = {
        "url": url,
        "outlet_id": outlet_id,
        "title": title,
        "summary": summary or None,
        "description": description,
        "published_at": _parse_published_at(article.get("date")),
        "image_url": (article.get("image_url") or "").strip() or None,
        "date_source": (article.get("date_source") or "").strip() or None,
        "raw_payload": article,
        "content_hash": _content_hash(title, summary),
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }
    if run_id:
        row["scrape_run_id"] = run_id
    return row


def _load_articles(path: Path) -> list[dict[str, Any]]:
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[WARN] Could not read {path}: {e}")
        return []
    if not isinstance(data, list):
        return []
    return [a for a in data if isinstance(a, dict)]


def _resolve_targets(
    *,
    gha_only: bool,
    local_only: bool,
    outlet_filter: list[str] | None,
) -> list[str]:
    if outlet_filter:
        return outlet_filter
    if gha_only:
        return list(GHA_SCRAPER_IDS)
    if local_only:
        return list(LOCAL_SCRAPER_IDS)
    return [s.id for s in SCRAPERS]


def _collect_files(data_dir: Path, outlet_ids: list[str]) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    for outlet_id in outlet_ids:
        data_file = OUTLET_DATA_FILES.get(outlet_id)
        if not data_file:
            print(f"[WARN] Unknown outlet id: {outlet_id}")
            continue
        path = data_dir / data_file
        if path.is_file():
            files.append((outlet_id, path))
        else:
            print(f"[SKIP] Missing {path}")
    return files


def _create_pipeline_run(client: Any, *, github_run_id: str | None) -> str:
    payload = {
        "run_type": "scrape",
        "status": "running",
        "github_run_id": github_run_id,
        "metadata": {"source": "ingest_raw.py"},
    }
    resp = client.table("pipeline_runs").insert(payload).execute()
    return resp.data[0]["id"]


def _finish_pipeline_run(
    client: Any,
    run_id: str,
    *,
    articles_in: int,
    articles_out: int,
    status: str = "completed",
    error_message: str | None = None,
) -> None:
    client.table("pipeline_runs").update(
        {
            "status": status,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "articles_in": articles_in,
            "articles_out": articles_out,
            "error_message": error_message,
        }
    ).eq("id", run_id).execute()


def ingest(
    *,
    data_dir: Path,
    gha_only: bool = False,
    local_only: bool = False,
    outlet_filter: list[str] | None = None,
    dry_run: bool = False,
) -> int:
    load_dotenv(APP_DIR / ".env")

    if gha_only and local_only:
        print("[ERROR] Use only one of --gha-only or --local-only")
        return 1

    outlet_ids = _resolve_targets(
        gha_only=gha_only,
        local_only=local_only,
        outlet_filter=outlet_filter,
    )
    targets = _collect_files(data_dir, outlet_ids)
    if not targets:
        print("[WARN] No JSON files to ingest")
        return 0

    if dry_run:
        all_rows: list[dict[str, Any]] = []
        articles_in = 0
        seen_urls: set[str] = set()
        for outlet_id, path in targets:
            articles = _load_articles(path)
            articles_in += len(articles)
            for article in articles:
                row = _article_row(article, outlet_id, None)
                if row and row["url"] not in seen_urls:
                    seen_urls.add(row["url"])
                    all_rows.append(row)
            print(f"[{outlet_id}] {len(articles)} article(s) in {path.name}")
        print(f"[DRY RUN] Would upsert {len(all_rows)} article(s) from {len(targets)} file(s)")
        return 0

    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        print("[ERROR] Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in env or timeTwister/.env")
        return 1

    from supabase import create_client

    client = create_client(url, key)
    github_run_id = os.getenv("GITHUB_RUN_ID")
    run_id: str | None = None
    if not dry_run:
        run_id = _create_pipeline_run(client, github_run_id=github_run_id)

    all_rows: list[dict[str, Any]] = []
    articles_in = 0
    seen_urls: set[str] = set()

    for outlet_id, path in targets:
        articles = _load_articles(path)
        articles_in += len(articles)
        if not articles:
            print(f"[{outlet_id}] 0 articles in {path.name}")
            continue

        outlet_rows: list[dict[str, Any]] = []
        for article in articles:
            row = _article_row(article, outlet_id, run_id)
            if not row or row["url"] in seen_urls:
                continue
            seen_urls.add(row["url"])
            outlet_rows.append(row)

        print(f"[{outlet_id}] {len(outlet_rows)} row(s) from {path.name}")
        all_rows.extend(outlet_rows)

    if not all_rows:
        print("[INFO] No articles to upsert")
        if run_id:
            _finish_pipeline_run(client, run_id, articles_in=articles_in, articles_out=0)
        return 0

    batch_size = 100
    upserted = 0
    for i in range(0, len(all_rows), batch_size):
        batch = all_rows[i : i + batch_size]
        client.table("raw_articles").upsert(batch, on_conflict="url").execute()
        upserted += len(batch)

    print(f"[OK] Upserted {upserted} article(s) into raw_articles")
    if run_id:
        _finish_pipeline_run(
            client,
            run_id,
            articles_in=articles_in,
            articles_out=upserted,
        )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest scraper JSON into Supabase raw_articles")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DATA_DIR,
        help="Directory containing *_latest_news.json files",
    )
    parser.add_argument(
        "--gha-only",
        action="store_true",
        help="Ingest only outlets on the GHA matrix (default when GITHUB_ACTIONS=true)",
    )
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="Ingest only local/home-IP outlets",
    )
    parser.add_argument(
        "--outlets",
        nargs="+",
        metavar="ID",
        help="Specific outlet ids (e.g. dailymirror virakesari)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Parse files without writing to Supabase")
    args = parser.parse_args()

    gha_only = args.gha_only
    if not gha_only and not args.local_only and not args.outlets:
        if os.getenv("GITHUB_ACTIONS", "").lower() == "true":
            gha_only = True

    outlet_filter = None
    if args.outlets:
        outlet_filter = []
        for name in args.outlets:
            key = name.lower().strip()
            if key not in SCRAPER_BY_ID:
                print(f"[ERROR] Unknown outlet: {name}")
                sys.exit(1)
            outlet_filter.append(key)

    code = ingest(
        data_dir=args.data_dir,
        gha_only=gha_only,
        local_only=args.local_only,
        outlet_filter=outlet_filter,
        dry_run=args.dry_run,
    )
    sys.exit(code)


if __name__ == "__main__":
    main()
