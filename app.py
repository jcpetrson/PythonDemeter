#!/usr/bin/env python3
import json
import os
import re
import sqlite3

from flask import Flask, g, render_template, request, abort

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_DB_PATH = os.path.join(_BASE_DIR, "data", "index.db")

PER_PAGE = 25

COMMON_FORMATS = [
    'epub', 'pdf', 'mobi', 'azw3', 'fb2', 'lit',
    'azw', 'cbr', 'cbz', 'djvu', 'txt', 'kepub',
]

app = Flask(__name__)


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(
            f"file:{INDEX_DB_PATH}?mode=ro",
            uri=True,
            timeout=30,
            check_same_thread=False,
        )
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(e=None):
    db = g.pop('db', None)
    if db:
        db.close()


def parse_title(raw):
    try:
        obj = json.loads(raw or '{}')
        return obj.get('label') or raw or ''
    except Exception:
        return raw or ''


def parse_title_href(raw):
    try:
        obj = json.loads(raw or '{}')
        return obj.get('href', '')
    except Exception:
        return ''


def parse_authors(raw):
    try:
        lst = json.loads(raw or '[]')
        if isinstance(lst, list):
            return ', '.join(lst)
        return raw or ''
    except Exception:
        return raw or ''


def parse_formats(raw):
    try:
        lst = json.loads(raw or '[]')
        return lst if isinstance(lst, list) else []
    except Exception:
        return []


def parse_links(raw):
    try:
        lst = json.loads(raw or '[]')
        return lst if isinstance(lst, list) else []
    except Exception:
        return []


def parse_cover(raw):
    try:
        obj = json.loads(raw or '{}')
        return obj.get('img_src', '')
    except Exception:
        return ''


def make_fts_query(q):
    cleaned = re.sub(r'["\(\)\*:\^\\]', ' ', q)
    words = cleaned.split()
    return ' '.join(words)


def enrich_row(row):
    d = dict(row)
    d['title_text'] = parse_title(d.get('title'))
    d['title_href'] = parse_title_href(d.get('title'))
    d['authors_text'] = parse_authors(d.get('authors'))
    d['formats_list'] = parse_formats(d.get('formats'))
    d['links_list'] = parse_links(d.get('links'))
    d['cover_url'] = parse_cover(d.get('cover'))
    return d


def do_search(q, fmt, page):
    db = get_db()
    offset = (page - 1) * PER_PAGE
    fts_q = make_fts_query(q)
    if not fts_q:
        return [], 0

    fmt_filter = fmt.lower() if fmt else ''
    cols = 's.uuid, s.title, s.authors, s.formats, s.year, s.series, s.links, s.cover'

    if fmt_filter:
        sql = f"""
            SELECT {cols}
            FROM summary_fts f
            JOIN summary s ON s.rowid = f.rowid
            WHERE summary_fts MATCH ?
              AND EXISTS (SELECT 1 FROM json_each(s.formats) WHERE value = ?)
            ORDER BY rank
            LIMIT ? OFFSET ?
        """
        count_sql = """
            SELECT COUNT(*) FROM (
                SELECT f.rowid
                FROM summary_fts f
                JOIN summary s ON s.rowid = f.rowid
                WHERE summary_fts MATCH ?
                  AND EXISTS (SELECT 1 FROM json_each(s.formats) WHERE value = ?)
                LIMIT 5000
            ) t
        """
        rows = db.execute(sql, (fts_q, fmt_filter, PER_PAGE, offset)).fetchall()
        total = db.execute(count_sql, (fts_q, fmt_filter)).fetchone()[0]
    else:
        sql = f"""
            SELECT {cols}
            FROM summary_fts f
            JOIN summary s ON s.rowid = f.rowid
            WHERE summary_fts MATCH ?
            ORDER BY rank
            LIMIT ? OFFSET ?
        """
        count_sql = """
            SELECT COUNT(*) FROM (
                SELECT rowid FROM summary_fts WHERE summary_fts MATCH ?
                LIMIT 5000
            ) t
        """
        rows = db.execute(sql, (fts_q, PER_PAGE, offset)).fetchall()
        total = db.execute(count_sql, (fts_q,)).fetchone()[0]

    return [enrich_row(r) for r in rows], total


@app.route('/')
def search():
    q = request.args.get('q', '').strip()
    fmt = request.args.get('fmt', '').strip().lower()
    try:
        page = max(1, int(request.args.get('page', 1) or 1))
    except (ValueError, TypeError):
        page = 1

    books, total, error = [], 0, None
    if q:
        try:
            books, total = do_search(q, fmt, page)
        except sqlite3.OperationalError as e:
            error = f"Search error: {e}"

    total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)

    return render_template(
        'index.html',
        q=q, fmt=fmt, page=page,
        books=books, total=total,
        total_pages=total_pages,
        per_page=PER_PAGE,
        formats=COMMON_FORMATS,
        error=error,
    )


@app.route('/book/<uuid>')
def book_detail(uuid):
    db = get_db()
    row = db.execute(
        '''SELECT uuid, cover, title, authors, year, series, language,
                  links, publisher, tags, identifiers, formats
           FROM summary WHERE uuid = ?''',
        (uuid,),
    ).fetchone()

    if row is None:
        abort(404)

    book = enrich_row(row)
    try:
        tags = json.loads(row['tags'] or '[]')
        book['tags_list'] = tags if isinstance(tags, list) else []
    except Exception:
        book['tags_list'] = []
    try:
        idents = json.loads(row['identifiers'] or '{}')
        book['identifiers_dict'] = idents if isinstance(idents, dict) else {}
    except Exception:
        book['identifiers_dict'] = {}

    return render_template('book.html', book=book)


if __name__ == '__main__':
    app.run(debug=True, host='127.0.0.1', port=5000)
