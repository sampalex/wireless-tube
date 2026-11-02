# Wireless Tube (v1)

A wireless-only video and reading aggregator built with the Python 3 standard library and SQLite (no pip installs).
It covers 5G, 6G, Wi-Fi 6/6E/7/8, MAC/PHY, signal processing, beamforming and massive MIMO.
It starts from 109 retrieved seed links and adds new items found by a daily checker.

## Quick start
    python3 seed_import.py        # idempotent; loads seed_links.json into wireless_tube.db
    python3 check.py              # fetch all sources, then build site/
    python3 verify_site.py        # cross-check site/ against the DB (exit 1 on mismatch)
    bash run_daily.sh             # all of the above; this is the daily entry point

Other commands:
- `python3 check.py --only arxiv,youtube` runs a subset of sources.
- `python3 check.py --no-build` fetches without rebuilding the site.
- `python3 -m wtube.build` rebuilds site/ from the DB without fetching.

## Outputs (site/)
- `index.html`: a static page.
  - A "New today" section and an "Earlier this week" section.
  - Topic sections, with videos first in each topic.
  - New badges, which expire client-side 7 days after first seen.
- `rss.xml`: items first seen by the checker in the last 7 days.
- `data.json`: the full dataset, including the last run's per-source stats.
- `canvas.tsx`: the complete source for the "Wireless Tube" Page, with the data inlined.

## Sources (wtube/fetchers.py)
| name | what | notes |
|---|---|---|
| hn | HN Algolia `search_by_date` | `WT_HN_MIN_POINTS` (default 3) |
| arxiv | arXiv API: eess.SP, cs.IT, cs.NI plus keyword queries | rolling 7-day lookback (submission date lags the announcement); `WT_ARXIV_MAX` (default 300) |
| youtube | channel RSS for the 19 channels in channels.txt | no key needed |
| youtube_api | YouTube Data API search | runs only if `YOUTUBE_API_KEY` is set; otherwise status `skipped` |
| reddit | **disabled stub** | needs a registered Reddit app (OAuth client id/secret); never fetches |
| 3gpp | 3GPP news listing | 30-day lookback |
| nga | Next G Alliance highlights, press releases and white papers | undated; the first run is a silent baseline |
| ieeetv | IEEE.tv search pages | undated; the first run is a silent baseline |
ACM's Digital Library blocks scraping, so ACM content arrives through the ACM SIGCOMM and SIGMOBILE YouTube channels.

## Filter (wtube/filters.py)
- Include terms carry weights; EXCLUDE terms are a hard veto.
- Score ≥ 6 is kept; ≤ 2 is dropped.
- Scores from 3 to 5 are stored as `borderline` and not shown.
- The optional LLM review of borderline items is OFF. `WT_LLM_BORDERLINE=1` is the hook, and no model call is wired in v1.

## Dedupe (wtube/store.py)
- Stable keys: `yt:<id>`, `arxiv:<id>`, `doi:`, `hn:<id>`, otherwise `url:<sha1 of canonical URL>`.
- An `aliases` table maps alternate keys and URLs to one item. For example, an HN post maps to its target URL.
- Near-identical titles are also merged, except between two items that both have hard IDs in the same namespace (two different YouTube videos or arXiv papers).

## Badges
- `origin=seed`: never badged.
- `origin=baseline`: the first sighting by an undated scraper; never badged.
- `origin=check`: badged "New" for 7 days. It is "New today" if first seen in the last 24 h.

## Env vars
YOUTUBE_API_KEY, WT_LLM_BORDERLINE, WT_DB (default ./wireless_tube.db), WT_SITE (default ./site), WT_ARXIV_MAX, WT_HN_MIN_POINTS.

## Publishing
GitHub Actions (`.github/workflows/publish.yml`) deploys `site/` to GitHub Pages at `https://sampalex.github.io/wireless-tube/`.
- A push to `main` deploys the current `site/`.
- Every day at 13:00 UTC, and on manual `workflow_dispatch`, the workflow runs `bash run_daily.sh`. If the database or `site/` changed, it commits them as `github-actions[bot]` and deploys. That bot push does not start another checker run.
- Exit code 2 means every enabled source failed. The job fails and does not commit or deploy, so the live site is left as it is.
- `YOUTUBE_API_KEY` is an optional repository secret. When it is unset, the YouTube Data API source is skipped. Reddit stays disabled.
- Pages source must be GitHub Actions. The Actions token cannot create the Pages site (`Resource not accessible by integration`). The owner sets Settings → Pages → Build and deployment → Source to GitHub Actions once, then re-runs the "Check and publish" workflow. Later pushes and the daily run deploy on their own.
Links inside `site/` are relative (`rss.xml`, `data.json`), so they resolve under the `/wireless-tube/` subpath.

## Page refresh
- The published Page is "Wireless Tube", canvas_id `de6df849-719d-45e9-9d08-0c3a1b36c96a`.
- After `run_daily.sh` exits 0, call WriteCanvas with that canvas_id and the full contents of `site/canvas.tsx`. Omit the title.
