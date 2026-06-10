# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Tool

```bash
# Basic scrape
python demeter.py scrape run -d books -e epub

# Filter by author or title (SQL LIKE patterns)
python demeter.py scrape run -a "%Brad Thor%" -t "%Python%"

# Host management
python demeter.py host list
python demeter.py host add <url>
python demeter.py host enable <id>

# Download management
python demeter.py dl list
python demeter.py dl add <hash>
```

No build step, no package installation — runs directly with Python 3 and requires `requests` plus standard library.

## Architecture

Single-file application: all logic lives in `demeter.py` (~625 lines).

**Two SQLite databases** (under `./data/`):
- `sites.db` — Calibre host registry (url, status, active flag, scrape stats, country code)
- `index.db` — Downloaded book metadata (uuid, title, authors, formats, download links); opened in WAL mode

**CLI structure** uses argparse with three subcommand groups: `dl`, `host`, `scrape`. Each subcommand maps to a `handle_*()` function.

**Core scrape flow** (`handle_scrape_run`):
1. Queries `index.db` for books matching extension/author/title filters
2. Skips books already downloaded (cross-references `sites.db`)
3. Dispatches concurrent downloads via `ThreadPoolExecutor` (default 10 workers)
4. Updates host stats (downloads, scrapes, last_scrape timestamp)
5. Enforces 12-hour per-host scrape cooldown

**`download_book()`** performs HTTP GET with a configurable user-agent, parses `Title - Author` from link labels, sanitizes filenames (strips noise patterns), and writes files as `[title]_[author].[ext]` under the output directory.

## Key Conventions

- Database connections use `timeout=30, check_same_thread=False` for thread safety during concurrent downloads.
- Host eligibility for scraping: `status IN ('online', 'active') AND active = 1`.
- Filename sanitization uses regex to strip noise (e.g., trailing numbers, bracketed metadata) before writing to disk.
- `demeter_id` in `sites.db` is managed manually with `MAX(demeter_id) + 1` on insert (no `AUTOINCREMENT`).
