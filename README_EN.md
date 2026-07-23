# 🍅 Fanqie Trend Tracker

> **Male channel first, female channel secondary** — daily Fanqie novel new-book ranks with AI trend briefs on GitHub Pages.

This repository is an independent fork. Upstream sync is disabled; data format and scraping targets are customized.

Dashboard: https://6leokk.github.io/fanqietrend-main/

## Channels

| Channel | Rank URL prefix | Books per category |
|---------|-----------------|--------------------|
| Male (primary) | `/rank/1_1_*` | Top 30 |
| Female (secondary) | `/rank/0_1_*` | Top 15 |

Snapshot files: `data/fanqie_ranks_YYYYMMDD.json`

## Quick start

```bash
pip install -r requirements.txt
playwright install chromium
python scrape_fanqie_ranks.py
python scripts/build_latest.py
```

GitHub Actions runs daily at UTC 00:17 and deploys Pages.

Optional secrets for AI: `API_BASE_URL`, `API_KEY`, `API_MODEL`.
