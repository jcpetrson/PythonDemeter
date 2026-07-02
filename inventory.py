#!/usr/bin/env python3
"""Fetch a Calibre server's catalog metadata into the local index.db.

This is the inventory-ingest step the repo otherwise lacks. demeter.py only
downloads files for books already catalogued, and index.db is otherwise
populated externally from OpenCalibre CSV exports. This module queries a
Calibre content server's AJAX API and upserts book metadata into `summary`
(and syncs the external-content FTS index) so new books become searchable
and, later, downloadable via the stored /get/ links.
"""
import argparse
import json
import os
import sys
import time
from urllib.parse import urlparse

import requests

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_DB_PATH = os.path.join(_BASE_DIR, "data", "index.db")

USER_AGENT = "demeter-inventory / v1"
REQUEST_TIMEOUT = 30
BOOKS_PER_REQUEST = 200
FTS_COLUMNS = ("title", "authors", "series", "language",
               "identifiers", "tags", "publisher", "formats", "year")


def _get_json(base, path, **params):
    resp = requests.get(f"{base}{path}", params=params,
                        headers={"User-Agent": USER_AGENT},
                        timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _human_size(num):
    if not num:
        return ""
    for unit in ("B", "kB", "MB", "GB"):
        if num < 1024:
            return f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"


def get_libraries(base):
    info = _get_json(base, "/ajax/library-info")
    return list(info.get("library_map", {}).keys()), info.get("default_library")


def get_book_ids(base, library_id):
    count = _get_json(base, "/ajax/search", library_id=library_id, num=0)
    total = count.get("total_num", 0)
    if not total:
        return []
    data = _get_json(base, "/ajax/search", library_id=library_id,
                     num=total, offset=0)
    return data.get("book_ids", [])


def map_book(base, library_id, book_id, b):
    """Map one Calibre /ajax/books record to a summary row dict."""
    formats = b.get("formats") or []
    fmt_meta = b.get("format_metadata") or {}

    def label(fmt):
        size = (fmt_meta.get(fmt) or {}).get("size")
        return f"{fmt} ({_human_size(size)})" if size else fmt

    links = [{"href": f"{base}/get/{fmt}/{book_id}/{library_id}",
              "label": label(fmt)} for fmt in formats]
    languages = b.get("languages") or []
    pubdate = b.get("pubdate") or ""
    tags = b.get("tags") or []
    return {
        "uuid": b.get("uuid"),
        "cover": json.dumps({
            "img_src": f"{base}/get/thumb/{book_id}/{library_id}?sz=600x800",
            "width": 90,
        }),
        "title": json.dumps({
            "href": f"{base}#book_id={book_id}&library_id={library_id}&panel=book_details",
            "label": b.get("title") or "",
        }),
        "authors": json.dumps(b.get("authors") or []),
        "year": pubdate[:4] if pubdate else "",
        "series": b.get("series"),
        "language": languages[0] if languages else "",
        "links": json.dumps(links),
        "publisher": b.get("publisher"),
        "tags": json.dumps(tags) if tags else None,
        "identifiers": json.dumps(b.get("identifiers") or {}),
        "formats": json.dumps(formats),
    }


def fetch_books(base, library_id, book_ids, dry_run_limit=None):
    """Yield mapped summary rows for the given book ids, in batches."""
    if dry_run_limit:
        book_ids = book_ids[:dry_run_limit]
    for start in range(0, len(book_ids), BOOKS_PER_REQUEST):
        batch = book_ids[start:start + BOOKS_PER_REQUEST]
        ids_param = ",".join(str(i) for i in batch)
        data = _get_json(base, "/ajax/books", ids=ids_param,
                         library_id=library_id)
        for book_id, meta in data.items():
            if not meta:
                continue
            row = map_book(base, library_id, book_id, meta)
            if not row["uuid"]:
                continue
            yield row
        print(f"  fetched {min(start + BOOKS_PER_REQUEST, len(book_ids))}"
              f"/{len(book_ids)} books")


def upsert_rows(conn, rows):
    cols = ["uuid", "cover", "title", "authors", "year", "series",
            "language", "links", "publisher", "tags", "identifiers", "formats"]
    placeholders = ", ".join("?" for _ in cols)
    sql = f"INSERT OR REPLACE INTO summary ({', '.join(cols)}) VALUES ({placeholders})"
    conn.executemany(sql, [[r[c] for c in cols] for r in rows])


def rebuild_fts(conn):
    conn.execute("INSERT INTO summary_fts(summary_fts) VALUES('rebuild')")


def normalize_base(host_url):
    if "://" not in host_url:
        host_url = "http://" + host_url
    return host_url.rstrip("/")


def run(host_url, library=None, dry_run=None, rebuild=True):
    import sqlite3
    base = normalize_base(host_url)
    print(f"Host: {base}")

    libraries, default_lib = get_libraries(base)
    print(f"Libraries: {libraries} (default: {default_lib})")
    targets = [library] if library else libraries

    all_rows = []
    for lib in targets:
        ids = get_book_ids(base, lib)
        print(f"Library '{lib}': {len(ids)} books")
        rows = list(fetch_books(base, lib, ids, dry_run_limit=dry_run))
        all_rows.extend(rows)

    if dry_run:
        print(f"\n--- DRY RUN: mapped {len(all_rows)} books (no DB write) ---")
        for r in all_rows[:dry_run]:
            print(json.dumps(r, indent=2, ensure_ascii=False))
        return

    print(f"\nUpserting {len(all_rows)} books into summary ...")
    conn = sqlite3.connect(INDEX_DB_PATH, timeout=120)
    try:
        conn.execute("PRAGMA busy_timeout=120000")
        before = conn.execute("SELECT COUNT(*) FROM summary").fetchone()[0]
        upsert_rows(conn, all_rows)
        conn.commit()
        after = conn.execute("SELECT COUNT(*) FROM summary").fetchone()[0]
        print(f"summary rows: {before} -> {after} (+{after - before} new; "
              f"{len(all_rows) - (after - before)} refreshed)")

        if rebuild:
            print("Rebuilding FTS index ...")
            t0 = time.time()
            rebuild_fts(conn)
            conn.commit()
            print(f"FTS rebuild took {time.time() - t0:.1f}s")
        else:
            print("Skipping FTS rebuild (--no-rebuild); rebuild separately.")
    finally:
        conn.close()
    print("Done.")


def main():
    parser = argparse.ArgumentParser(
        description="Fetch a Calibre server's catalog into local index.db")
    parser.add_argument("host_url",
                        help="Calibre host, e.g. http://68.194.16.9:8010")
    parser.add_argument("-l", "--library",
                        help="specific library_id (default: all libraries)")
    parser.add_argument("--dry-run", type=int, metavar="N",
                        help="map first N books and print them, no DB write")
    parser.add_argument("--no-rebuild", action="store_true",
                        help="skip FTS rebuild (for batch runs; rebuild once after)")
    args = parser.parse_args()
    try:
        run(args.host_url, library=args.library, dry_run=args.dry_run,
            rebuild=not args.no_rebuild)
    except requests.RequestException as e:
        print(f"Network error talking to {args.host_url}: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
