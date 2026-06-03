# Deploying Scrapers to GitHub Actions

## All scrapers (recommended)

Workflow: **`.github/workflows/scrape-all.yml`**

| Type | Outlets | Mode |
|------|---------|------|
| **Incremental (FT.lk-style)** | ftlk, aruna, sundaytimes, island | `--incremental`, replace JSON, checkpoint stop |
| **Date-range (for now)** | other 13 | yesterday–today, `xvfb-run` on CI |

**Run:** Actions → **Scrape all news outlets** → choose `all` or one outlet → Run workflow.

---

## FT.lk only (legacy single-outlet workflow)

## What this sets up

- **Incremental scrape** every 30 minutes (configurable)
- Stops automatically when the last saved article is seen on the live feed
- Saves `data/ftlk_latest_news.json` + `data/ftlk_latest_news_checkpoint.json`
- Commits updated JSON back to the repo after each run (optional)

---

## Step 1 — Push the repo to GitHub

If you haven't already:

```powershell
cd "d:\ML Projects\VR\New folder"
git init
git add .
git commit -m "initial commit"
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git push -u origin main
```

> Make sure `.env` and `credentials.json` are in `.gitignore` before pushing.

---

## Step 2 — Create a `.gitignore`

Create `d:\ML Projects\VR\New folder\.gitignore` with at least:

```
.env
credentials.json
*.pyc
__pycache__/
```

---

## Step 3 — Add repository secrets (if you need Gemini for translate/categorize later)

1. Go to your repo on GitHub
2. **Settings → Secrets and variables → Actions → New repository secret**
3. Add:

| Secret name     | Value                        |
|-----------------|------------------------------|
| `GEMINI_API_KEY`| Your Google Gemini API key   |

The FT.lk scraper itself doesn't call Gemini, so this is only needed if you later add the translate/categorize steps.

---

## Step 4 — Verify the workflow file is in place

The workflow is at:
```
.github/workflows/scrape-ftlk.yml
```

Push it:
```powershell
git add .github/workflows/scrape-ftlk.yml timeTwister/requirements.txt
git commit -m "ci: add FT.lk incremental scrape workflow"
git push
```

---

## Step 5 — Test it manually first (before waiting for the schedule)

1. Go to your repo on GitHub
2. Click **Actions** tab
3. Click **"Scrape FT.lk (incremental)"** on the left
4. Click **"Run workflow"** (top right)
5. Leave mode as `incremental`, click **Run workflow**

Watch the run live. You should see:

```
[INCREMENTAL] Checkpoint (from JSON[0]): Home Lands' flagship...
[INCREMENTAL] FT.lk — stop when last scraped article is detected
...
[INFO] New article 1: ...
[INCREMENTAL] Reached last scraped article — stopping.
[INCREMENTAL] Saved 25 total articles (3 new this run) → data/ftlk_latest_news.json
```

---

## Step 6 — Test date-range mode (optional)

1. Run workflow → set **mode** = `date-range`
2. Set **start_date** = `2026-06-01`, **end_date** = `2026-06-03`
3. Check the artifact downloaded from the run

---

## Adjusting the schedule

In `.github/workflows/scrape-ftlk.yml`, change the cron line:

```yaml
- cron: "*/30 * * * *"   # every 30 min  (current)
- cron: "*/10 * * * *"   # every 10 min  (fastest GitHub allows)
- cron: "0 * * * *"      # every hour
- cron: "0 */6 * * *"    # every 6 hours
```

> **Note:** GitHub's minimum schedule interval is 5 minutes. Jobs are not guaranteed to run at exact times under high load.

---

## Where the data goes

| Option | How |
|--------|-----|
| **Committed to repo** (default) | The workflow does `git commit + push` if new articles found |
| **Artifact only** | Remove the "Commit updated data" step; download from Actions UI |
| **Supabase / Postgres** | Add an `ingest.py` step after the scraper (recommended for a live site) |

---

## Troubleshooting

### Chrome version mismatch
The `setup-chrome` action installs the latest stable Chrome. `webdriver-manager` auto-downloads the matching ChromeDriver. If you see version errors, update `webdriver-manager`:
```
pip install --upgrade webdriver-manager
```

### "Nothing to commit" every run
Means no new articles were found since last run — expected behaviour. The scraper stopped at the checkpoint.

### Scraper exits immediately (empty feed)
The checkpoint URL is no longer appearing on the live page (article was deleted/moved). Delete the checkpoint file and re-run:
```powershell
Remove-Item timeTwister/data/ftlk_latest_news_checkpoint.json
```
Next run will fall back to `data[0]` of the JSON, or bootstrap if the JSON is empty.

### `ModuleNotFoundError: incremental`
The scraper must be run from the `timeTwister/` directory (the workflow's `working-directory: timeTwister` handles this). If running locally, always:
```powershell
cd timeTwister
python scrapers/ftlk_selenium_json.py --incremental
```

---

## Checking minutes usage (free tier)

GitHub gives **2,000 free minutes/month** for private repos (unlimited for public).

One FT.lk incremental run ≈ 5–15 min on `ubuntu-latest`.

| Schedule | Runs/month | ~Minutes used |
|----------|-----------|---------------|
| Every 30 min | ~1,440 | 7,200–21,600 ⚠️ |
| Every hour | 720 | 3,600–10,800 ⚠️ |
| Every 6 hours | 120 | 600–1,800 ✅ |

> For high-frequency scraping, a $5/month VPS + cron is more cost-effective than Actions.
