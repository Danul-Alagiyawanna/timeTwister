"""
Central registry for all outlet scrapers (GitHub Actions matrix + local runners).

incremental=True  → run with --incremental (FT.lk-style: checkpoint stop, replace JSON)
incremental=False → date-range scrape (yesterday–today); use xvfb on CI if not headless
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScraperSpec:
    id: str
    module: str
    data_file: str
    incremental: bool = False


# Order matches run_scrapers.py
SCRAPERS: tuple[ScraperSpec, ...] = (
    ScraperSpec("sundaytimes", "sundaytimes_selenium_json", "sundaytimes_latest_news.json", True),
    ScraperSpec("dailynews", "dailynews_selenium_json", "dailynews_latest_news.json", True),
    ScraperSpec("ceylontoday", "ceylontoday_selenium_json", "ceylontoday_finance.json", True),
    ScraperSpec("dailymirror", "dailymirror_selenium_json", "dailymirror_latest_news.json", True),
    ScraperSpec("ftlk", "ftlk_selenium_json", "ftlk_latest_news.json", True),
    ScraperSpec("economynext", "economynext_selenium_json", "economynext_latest_news.json", True),
    ScraperSpec("morning", "themorning_selenium_json", "themorning_latest_news.json", True),
    ScraperSpec("island", "island_selenium_json", "island_latest_news.json", True),
    ScraperSpec("sundayobserver", "sundayobserver_selenium_json", "sundayobserver_latest_news.json", True),
    ScraperSpec("dinamina", "dinamina_selenium_json", "dinamina_latest_news.json", True),
    ScraperSpec("divaina", "divaina_selenium_json", "divaina_latest_news.json", True),
    ScraperSpec("lankadeepa", "lankadeepa_selenium_json", "lankadeepa_latest_news.json", True),
    ScraperSpec("aruna", "aruna_selenium_json", "aruna_latest_news.json", True),
    ScraperSpec("mawbima", "mawbima_selenium_json", "mawbima_latest_news.json", True),
    ScraperSpec("virakesari", "virakesari_selenium_json", "virakesari_latest_news.json", True),
    ScraperSpec("thinakaran", "thinakaran_selenium_json", "thinakaran_latest_news.json", True),
    ScraperSpec("thamilan", "thamilan_selenium_json", "thamilan_latest_news.json", True),
)

INCREMENTAL_MODULES = frozenset(s.module for s in SCRAPERS if s.incremental)

SCRAPER_BY_ID = {s.id: s for s in SCRAPERS}
