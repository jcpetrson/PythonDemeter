#!/usr/bin/env python3
"""
One-time script to fetch literary award winners from Wikidata and save locally.
Run once:  python3 fetch_awards.py
Output:    data/awards.json
"""
import json
import os
from datetime import datetime

import requests

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATH = os.path.join(_BASE_DIR, "data", "awards.json")

# Confirmed Wikidata QIDs for major English-language literary awards
AWARDS = {
    'Q833633':  'Pulitzer Prize for Fiction',
    'Q255032':  'Hugo Award for Best Novel',
    'Q160082':  'Booker Prize',
    'Q708830':  'Arthur C. Clarke Award',
    'Q898527':  'World Fantasy Award for Best Novel',
    'Q595998':  'Locus Award for Best Novel',
}

SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"

QUERY = """
SELECT DISTINCT ?bookLabel ?authorLabel ?pubYear ?awardItem WHERE {
  VALUES ?awardItem { %(qids)s }
  ?book wdt:P166 ?awardItem .
  OPTIONAL { ?book wdt:P577 ?pubDate . BIND(YEAR(?pubDate) AS ?pubYear) }
  OPTIONAL { ?book wdt:P50 ?author }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
  FILTER(BOUND(?pubYear) && ?pubYear >= 1920 && ?pubYear <= 2025)
}
ORDER BY DESC(?pubYear) ?bookLabel
""" % {'qids': ' '.join(f'wd:{q}' for q in AWARDS)}


def fetch():
    print("Querying Wikidata SPARQL endpoint...")
    resp = requests.get(
        SPARQL_ENDPOINT,
        params={'query': QUERY, 'format': 'json'},
        headers={'User-Agent': 'WWWLibrary/1.0 (literary-awards-fetch)'},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()['results']['bindings']


def parse_qid(uri):
    return uri.rsplit('/', 1)[-1] if uri else ''


def build_entries(bindings):
    seen = set()
    entries = []
    for row in bindings:
        title  = row.get('bookLabel',   {}).get('value', '').strip()
        author = row.get('authorLabel', {}).get('value', '').strip()
        year   = row.get('pubYear',     {}).get('value', '')
        qid    = parse_qid(row.get('awardItem', {}).get('value', ''))
        award  = AWARDS.get(qid, qid)

        if not title or not year:
            continue
        # Skip Wikidata auto-labels like "Q12345"
        if title.startswith('Q') and title[1:].isdigit():
            continue
        key = (title.lower(), award, year)
        if key in seen:
            continue
        seen.add(key)
        entries.append({
            'title':  title,
            'author': author,
            'award':  award,
            'year':   int(year),
        })
    return sorted(entries, key=lambda e: (-e['year'], e['award'], e['title']))


def main():
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    bindings = fetch()
    print(f"  Got {len(bindings)} raw rows")
    entries = build_entries(bindings)
    print(f"  Cleaned to {len(entries)} entries across {len({e['year'] for e in entries})} years")

    payload = {
        'fetched_at': datetime.utcnow().isoformat() + 'Z',
        'awards': entries,
    }
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"Saved → {OUTPUT_PATH}")

    # Show a quick sample
    for entry in entries[:10]:
        print(f"  {entry['year']}  {entry['award']:40s}  {entry['title']} / {entry['author']}")


if __name__ == '__main__':
    main()
