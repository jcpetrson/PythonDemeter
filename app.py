#!/usr/bin/env python3
import csv
import json
import os
import re
import sqlite3
import unicodedata

from flask import Flask, g, render_template, request, abort

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_DB_PATH    = os.path.join(_BASE_DIR, "data", "index.db")
SITES_DB_PATH    = os.path.join(_BASE_DIR, "data", "sites.db")
GOODREADS_PATH   = os.path.join(_BASE_DIR, "goodreads_library_export.csv")

PER_PAGE = 25

COMMON_FORMATS = [
    'epub', 'pdf', 'mobi', 'azw3', 'fb2', 'lit',
    'azw', 'cbr', 'cbz', 'djvu', 'txt', 'kepub',
]

COMMON_LANGUAGES = [
    ('eng', 'English'),    # 722K
    ('spa', 'Spanish'),    # 289K
    ('ger', 'German'),     # 190K
    ('dut', 'Dutch'),      # 88K
    ('fre', 'French'),     # 66K
    ('chi', 'Chinese'),    # 32K
    ('cat', 'Catalan'),    # 19K
    ('ita', 'Italian'),    # 17K
    ('jpn', 'Japanese'),   # 8K
    ('urd', 'Urdu'),       # 4K
    ('rus', 'Russian'),    # 3K
    ('pol', 'Polish'),     # 3K
    ('cze', 'Czech'),      # 2K
    ('snd', 'Sindhi'),     # 773
    ('hun', 'Hungarian'),  # 282
    ('dan', 'Danish'),     # 181
    ('ara', 'Arabic'),     # 542
    ('por', 'Portuguese'), # 407
    ('lat', 'Latin'),      # 118
    ('swe', 'Swedish'),    # 115
]
# Covers ISO 639-2/B codes (stored in DB), /T alternates, and every code seen in the data
LANG_MAP = {
    'eng': 'English',      'spa': 'Spanish',     'ger': 'German',      'deu': 'German',
    'dut': 'Dutch',        'nld': 'Dutch',        'fre': 'French',      'fra': 'French',
    'chi': 'Chinese',      'zho': 'Chinese',      'cat': 'Catalan',     'ita': 'Italian',
    'jpn': 'Japanese',     'urd': 'Urdu',         'rus': 'Russian',     'pol': 'Polish',
    'cze': 'Czech',        'ces': 'Czech',        'hun': 'Hungarian',   'dan': 'Danish',
    'ara': 'Arabic',       'por': 'Portuguese',   'lat': 'Latin',       'swe': 'Swedish',
    'tur': 'Turkish',      'gre': 'Greek',        'ell': 'Greek',       'fin': 'Finnish',
    'nor': 'Norwegian',    'bul': 'Bulgarian',    'rum': 'Romanian',    'ron': 'Romanian',
    'wel': 'Welsh',        'epo': 'Esperanto',    'lit': 'Lithuanian',  'slv': 'Slovenian',
    'slo': 'Slovak',       'slk': 'Slovak',       'est': 'Estonian',    'glg': 'Galician',
    'swa': 'Swahili',      'mlt': 'Maltese',      'amh': 'Amharic',     'snd': 'Sindhi',
    'inh': 'Ingush',       'mlg': 'Malagasy',     'que': 'Quechua',     'srp': 'Serbian',
    'tam': 'Tamil',        'bre': 'Breton',       'xho': 'Xhosa',       'alb': 'Albanian',
    'sqi': 'Albanian',     'ukr': 'Ukrainian',    'oci': 'Occitan',     'ltz': 'Luxembourgish',
    'kaz': 'Kazakh',       'baq': 'Basque',       'eus': 'Basque',
}

_AWARDS_PATH = os.path.join(_BASE_DIR, "data", "awards.json")

def _load_awards():
    """Load locally-stored Wikidata award winners, keyed by year (int)."""
    if not os.path.exists(_AWARDS_PATH):
        return {}
    try:
        with open(_AWARDS_PATH, encoding='utf-8') as f:
            data = json.load(f)
        by_year = {}
        for entry in data.get('awards', []):
            y = entry.get('year')
            if y:
                by_year.setdefault(int(y), []).append(entry)
        return by_year
    except Exception:
        return {}

AWARDS_BY_YEAR = _load_awards()


def _gr_isbn(raw):
    return re.sub(r'[^0-9X]', '', raw or '')

def _strip_accents(s):
    return ''.join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c))

def _gr_title_key(title):
    t = _strip_accents((title or '').lower())
    t = re.sub(r'\s*\(.*?\)\s*$', '', t)
    return re.sub(r'\s+', ' ', t).strip()

def _gr_author_key(author):
    author = (author or '').strip()
    if ',' in author:
        surname = author.split(',')[0]
    else:
        parts = author.split()
        surname = parts[-1] if parts else author
    return re.sub(r'\s+', ' ', surname).strip().lower()

def _load_goodreads():
    if not os.path.exists(GOODREADS_PATH):
        return None
    by_isbn, by_title = {}, {}
    current, to_read, read, dnf = None, [], [], []
    with open(GOODREADS_PATH, encoding='utf-8') as f:
        for row in csv.DictReader(f):
            isbn13 = _gr_isbn(row.get('ISBN13', ''))
            title  = row.get('Title', '').strip()
            author = row.get('Author', '').strip()
            rating = int(row.get('My Rating') or 0)
            shelf  = row.get('Exclusive Shelf', '')
            entry  = {
                'title':      title,
                'author':     author,
                'rating':     rating,
                'shelf':      shelf,
                'date_read':  row.get('Date Read', ''),
                'date_added': row.get('Date Added', ''),
                'isbn13':     isbn13,
                'pages':      row.get('Number of Pages', ''),
                'year':       row.get('Original Publication Year') or row.get('Year Published', ''),
            }
            if isbn13:
                by_isbn[isbn13] = entry
            tk = (_gr_title_key(title), _gr_author_key(author))
            by_title[tk] = entry
            if shelf == 'currently-reading':
                current = entry
            elif shelf == 'to-read':
                to_read.append(entry)
            elif shelf == 'read':
                read.append(entry)
            elif shelf == 'did-not-finish':
                dnf.append(entry)
    read.sort(key=lambda e: e['date_read'] or '', reverse=True)
    return {'by_isbn': by_isbn, 'by_title': by_title,
            'current': current, 'to_read': to_read, 'read': read, 'dnf': dnf}

GOODREADS = _load_goodreads()


def _match_goodreads(d):
    if not GOODREADS:
        return None
    try:
        idents = json.loads(d.get('identifiers') or '{}')
        isbn = re.sub(r'[^0-9X]', '', idents.get('isbn', ''))
        if isbn and isbn in GOODREADS['by_isbn']:
            return GOODREADS['by_isbn'][isbn]
    except Exception:
        pass
    tk = (_gr_title_key(d.get('title_text') or ''),
          _gr_author_key(d.get('authors_text') or ''))
    return GOODREADS['by_title'].get(tk)


SORT_FIELDS = {
    'title':    "json_extract(s.title, '$.label')",
    'authors':  "json_extract(s.authors, '$[0]')",
    'year':     's.year',
    'language': 's.language',
}

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
    sdb = g.pop('sites_db', None)
    if sdb:
        sdb.close()


def get_sites_db():
    if 'sites_db' not in g:
        g.sites_db = sqlite3.connect(
            f"file:{SITES_DB_PATH}?mode=ro",
            uri=True,
            timeout=30,
            check_same_thread=False,
        )
        g.sites_db.row_factory = sqlite3.Row
    return g.sites_db


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
    if not d.get('language'):
        d['language'] = 'eng'
    d['goodreads'] = _match_goodreads(d)
    return d


def do_browse(fmt, lang, year, sort, order, page):
    db = get_db()
    offset = (page - 1) * PER_PAGE
    cols = 's.uuid, s.title, s.authors, s.formats, s.year, s.series, s.links, s.cover, s.language, s.identifiers'

    conditions = []
    params = []
    if fmt:
        conditions.append("EXISTS (SELECT 1 FROM json_each(s.formats) WHERE value = ?)")
        params.append(fmt)
    if lang == 'eng':
        conditions.append("(s.language = 'eng' OR s.language IS NULL OR s.language = '')")
    elif lang:
        conditions.append("s.language = ?")
        params.append(lang)
    if year:
        conditions.append("s.year = ?")
        params.append(year)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    order_clause = "json_extract(s.title, '$.label') ASC"
    if sort in SORT_FIELDS:
        direction = 'DESC' if order == 'desc' else 'ASC'
        order_clause = f'{SORT_FIELDS[sort]} {direction}'

    total = db.execute(f"SELECT COUNT(*) FROM summary s {where}", params).fetchone()[0]
    rows = db.execute(
        f"SELECT {cols} FROM summary s {where} ORDER BY {order_clause} LIMIT ? OFFSET ?",
        params + [PER_PAGE, offset],
    ).fetchall()
    return [enrich_row(r) for r in rows], total


def do_search(q, fmt, lang, year, sort, order, page):
    db = get_db()
    offset = (page - 1) * PER_PAGE
    fts_q = make_fts_query(q)
    if not fts_q:
        return [], 0

    fmt_filter = fmt.lower() if fmt else ''
    lang_filter = lang.lower() if lang else ''
    cols = 's.uuid, s.title, s.authors, s.formats, s.year, s.series, s.links, s.cover, s.language, s.identifiers'

    extra_where = ''
    extra_params = []
    if fmt_filter:
        extra_where += ' AND EXISTS (SELECT 1 FROM json_each(s.formats) WHERE value = ?)'
        extra_params.append(fmt_filter)
    if lang_filter == 'eng':
        extra_where += " AND (s.language = 'eng' OR s.language IS NULL OR s.language = '')"
    elif lang_filter:
        extra_where += ' AND s.language = ?'
        extra_params.append(lang_filter)
    if year:
        extra_where += ' AND s.year = ?'
        extra_params.append(year)

    order_clause = 'rank'
    if sort in SORT_FIELDS:
        direction = 'DESC' if order == 'desc' else 'ASC'
        order_clause = f'{SORT_FIELDS[sort]} {direction}'

    sql = f"""
        SELECT {cols}
        FROM summary_fts f
        JOIN summary s ON s.rowid = f.rowid
        WHERE summary_fts MATCH ?{extra_where}
        ORDER BY {order_clause}
        LIMIT ? OFFSET ?
    """
    count_sql = f"""
        SELECT COUNT(*) FROM (
            SELECT f.rowid
            FROM summary_fts f
            JOIN summary s ON s.rowid = f.rowid
            WHERE summary_fts MATCH ?{extra_where}
            LIMIT 5000
        ) t
    """
    rows = db.execute(sql, (fts_q, *extra_params, PER_PAGE, offset)).fetchall()
    total = db.execute(count_sql, (fts_q, *extra_params)).fetchone()[0]

    return [enrich_row(r) for r in rows], total


def get_home_stats():
    db = get_db()
    total = db.execute("SELECT COUNT(*) FROM summary").fetchone()[0]
    lang_rows = db.execute(
        "SELECT language, COUNT(*) as n FROM summary "
        "WHERE language IS NOT NULL AND language != '' "
        "GROUP BY language ORDER BY n DESC LIMIT 16"
    ).fetchall()
    year_rows = db.execute(
        "SELECT year, COUNT(*) as n FROM summary "
        "WHERE (language = 'eng' OR language IS NULL OR language = '') "
        "AND year GLOB '[12][0-9][0-9][0-9]' "
        "AND CAST(year AS INTEGER) BETWEEN 1800 AND 2025 "
        "GROUP BY year ORDER BY year DESC LIMIT 60"
    ).fetchall()
    # Build awards list for the most recent years that have data
    recent_award_years = sorted(
        (y for y in AWARDS_BY_YEAR if 1920 <= y <= 2025),
        reverse=True
    )[:15]
    awards_recent = {y: AWARDS_BY_YEAR[y] for y in recent_award_years}
    return {
        'total': total,
        'languages': [dict(r) for r in lang_rows],
        'years': [dict(r) for r in year_rows],
        'awards': awards_recent,
        'award_years': recent_award_years,
        'has_awards': bool(awards_recent),
        'currently_reading': GOODREADS['current'] if GOODREADS else None,
    }


@app.route('/my-library')
def my_library():
    ctx = dict(formats=COMMON_FORMATS, languages=COMMON_LANGUAGES, q='', fmt='', lang='')
    if not GOODREADS:
        return render_template('my_library.html',
                               current=None, read=[], to_read=[], dnf=[], **ctx)
    return render_template('my_library.html',
                           current=GOODREADS['current'],
                           read=GOODREADS['read'],
                           to_read=GOODREADS['to_read'],
                           dnf=GOODREADS['dnf'],
                           **ctx)


@app.route('/')
def search():
    q = request.args.get('q', '').strip()
    fmt = request.args.get('fmt', '').strip().lower()
    lang = request.args.get('lang', '').strip().lower()
    year = request.args.get('year', '').strip()
    sort = request.args.get('sort', '').strip().lower()
    order = request.args.get('order', 'asc').strip().lower()
    if year and not (year.isdigit() and 1800 <= int(year) <= 2025):
        year = ''
    if sort not in SORT_FIELDS:
        sort = ''
    if order not in ('asc', 'desc'):
        order = 'asc'
    try:
        page = max(1, int(request.args.get('page', 1) or 1))
    except (ValueError, TypeError):
        page = 1

    books, total, error = [], 0, None
    if q:
        try:
            books, total = do_search(q, fmt, lang, year, sort, order, page)
        except sqlite3.OperationalError as e:
            error = f"Search error: {e}"
    elif lang or fmt or year:
        try:
            books, total = do_browse(fmt, lang, year, sort, order, page)
        except sqlite3.OperationalError as e:
            error = f"Browse error: {e}"

    total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    is_home = not (q or lang or fmt or year)
    stats = get_home_stats() if is_home else None

    return render_template(
        'index.html',
        q=q, fmt=fmt, lang=lang, year=year, sort=sort, order=order, page=page,
        books=books, total=total,
        total_pages=total_pages,
        per_page=PER_PAGE,
        formats=COMMON_FORMATS,
        languages=COMMON_LANGUAGES,
        lang_map=LANG_MAP,
        stats=stats,
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

    return render_template('book.html', book=book, lang_map=LANG_MAP,
                           formats=COMMON_FORMATS, languages=COMMON_LANGUAGES,
                           q='', fmt='', lang='')


@app.route('/sites')
def sites_list():
    db = get_sites_db()
    status_filter = request.args.get('status', '').strip()
    country_filter = request.args.get('country', '').strip().upper()

    params = []
    where = []
    if status_filter:
        where.append('status = ?')
        params.append(status_filter)
    if country_filter:
        where.append('country = ?')
        params.append(country_filter)

    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''
    rows = db.execute(
        f'''SELECT uuid, url, country, status, book_count, libraries_count,
                   new_books, last_check, last_online, active, failed_attempts
            FROM sites {where_sql}
            ORDER BY book_count DESC''',
        params,
    ).fetchall()

    all_statuses = [r[0] for r in db.execute(
        "SELECT DISTINCT status FROM sites WHERE status IS NOT NULL ORDER BY status"
    ).fetchall()]
    all_countries = [r[0] for r in db.execute(
        "SELECT DISTINCT country FROM sites WHERE country IS NOT NULL ORDER BY country"
    ).fetchall()]
    total_count = db.execute("SELECT COUNT(*) FROM sites").fetchone()[0]

    return render_template(
        'sites.html',
        sites=rows,
        status_filter=status_filter,
        country_filter=country_filter,
        all_statuses=all_statuses,
        all_countries=all_countries,
        total_count=total_count,
    )


@app.route('/sites/<uuid>')
def site_detail(uuid):
    db = get_sites_db()
    row = db.execute('SELECT * FROM sites WHERE uuid = ?', (uuid,)).fetchone()
    if row is None:
        abort(404)

    libraries = db.execute(
        '''SELECT library, book_count_per_library, new_books_per_library, last_updated
           FROM libraries_per_server WHERE url = ? ORDER BY book_count_per_library DESC''',
        (row['url'],),
    ).fetchall()

    return render_template('site_detail.html', site=row, libraries=libraries)


if __name__ == '__main__':
    app.run(debug=True, host='127.0.0.1', port=5000)
