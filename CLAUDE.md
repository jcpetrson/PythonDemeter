# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Tools

```bash
# Run the Flask web UI
python app.py
# Accessible at http://127.0.0.1:5000

# Basic scrape (CLI)
python demeter.py scrape run -d books -e epub

# Filter by author or title (SQL LIKE patterns)
python demeter.py scrape run -a "%Brad Thor%" -t "%Python%"

# Host management
python demeter.py host list
python demeter.py host list-all
python demeter.py host add <url>
python demeter.py host enable <id>
python demeter.py host enable --enable-all
python demeter.py host enable --enable-country US
python demeter.py host disable <id>
python demeter.py host disable --disable-all
python demeter.py host stats <id>

# Download management
python demeter.py dl list
python demeter.py dl add <hash>
```

No build step. Dependencies: `pip install flask requests` (or `pip install -r requirements.txt`).

## Architecture

This project has two entry points that share the same two SQLite databases under `./data/`:

- **`demeter.py`** — CLI scraper (~625 lines). Queries `index.db` for book links matching the active hosts in `sites.db`, downloads files concurrently via `ThreadPoolExecutor`, and updates scrape stats. Enforces a 12-hour per-host cooldown.
- **`app.py`** — Flask web UI (~295 lines). Read-only view over both databases. Routes: `/` (FTS book search), `/book/<uuid>` (book detail), `/sites` (site list with status/country filters), `/sites/<uuid>` (site detail with per-library breakdown).

**Two SQLite databases** (under `./data/`):
- `sites.db` — Calibre host registry: `sites` table (uuid, url, status, active, country, book_count, scrape stats) and `libraries_per_server` table (per-library breakdown used by `/sites/<uuid>`).
- `index.db` — Book metadata: `summary` table (uuid, title, authors, formats, links, cover — JSON-encoded fields) and `summary_fts` virtual FTS5 table for full-text search. Opened in WAL mode.

## Key Conventions

- **JSON-encoded columns**: `title`, `authors`, `formats`, `links`, and `cover` in `index.db` are stored as JSON strings. `app.py` decodes them with `parse_title()`, `parse_authors()`, etc. before passing to templates.
- **FTS search**: `do_search()` in `app.py` joins `summary_fts` to `summary` on `rowid` and passes user input through `make_fts_query()` which strips FTS special characters before querying.
- **Thread-safe DB connections**: `demeter.py` opens a fresh `index.db` connection per download thread. `app.py` uses Flask `g` for per-request connections, both opened read-only via `?mode=ro` URI.
- **Host eligibility**: `status IN ('online', 'active') AND active = 1`.
- **`demeter_id`** in `sites.db` is assigned manually with `MAX(demeter_id) + 1` (no `AUTOINCREMENT`).
- **Filename sanitization** in `download_book()`: strips Calibre URL-encoded noise patterns from link labels, splits on ` - ` / ` — ` / ` by ` to extract title and author, then sanitizes to `[title]_[author].[ext]`.
