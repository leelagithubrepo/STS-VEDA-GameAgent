#!/usr/bin/env python3
"""Validate the static public site and produce the identical Sites output.

GitHub Pages serves docs/; Sites serves dist/. Only aggregate learning totals
enter this public build; private decisions and screenshots never do. This
performs file/link checks, not browser or visual testing.
"""
from datetime import datetime, timezone
from html.parser import HTMLParser
import sqlite3
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import urlsplit, unquote

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / 'docs'
DATABASE = ROOT / 'artifacts' / 'veda-memory.sqlite3'


def _count(connection, query):
    try:
        return int(connection.execute(query).fetchone()[0])
    except sqlite3.Error:
        return 0


def public_learning_metrics():
    """Return aggregate-only telemetry safe for the public static site."""
    values = {'decisions': 0, 'resolved': 0, 'outcomes': 0, 'floors': 0,
              'victory_floors': 0, 'builder_verified': 0}
    if DATABASE.is_file():
        with sqlite3.connect(DATABASE) as connection:
            values.update(
                decisions=_count(connection, 'SELECT COUNT(*) FROM decisions'),
                resolved=_count(connection, "SELECT COUNT(*) FROM decisions WHERE status='resolved'"),
                outcomes=_count(connection, "SELECT COUNT(*) FROM evidence_events WHERE kind='play_outcome'"),
                floors=_count(connection, 'SELECT COUNT(*) FROM floors'),
                victory_floors=_count(connection, "SELECT COUNT(*) FROM floors WHERE outcome='victory'"),
                builder_verified=_count(connection, "SELECT COUNT(*) FROM builder_requests WHERE status='verified'"),
            )
    values['updated'] = datetime.now(timezone.utc).date().isoformat()
    return values


def _public_html(text, metrics):
    replacements = {
        '__VEDA_DECISIONS__': str(metrics['decisions']),
        '__VEDA_RESOLVED__': str(metrics['resolved']),
        '__VEDA_OUTCOMES__': str(metrics['outcomes']),
        '__VEDA_FLOORS__': str(metrics['floors']),
        '__VEDA_VICTORY_FLOORS__': str(metrics['victory_floors']),
        '__VEDA_BUILDER_VERIFIED__': str(metrics['builder_verified']),
        '__VEDA_METRICS_UPDATED__': metrics['updated'],
    }
    for marker, value in replacements.items():
        text = text.replace(marker, value)
    if '__VEDA_' in text:
        raise ValueError('unresolved public telemetry marker')
    return text


class Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.links=[]; self.ids=set()
    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if 'id' in attrs:
            if attrs['id'] in self.ids: raise ValueError('duplicate HTML id: '+attrs['id'])
            self.ids.add(attrs['id'])
        for name in ('href','src','poster'):
            if attrs.get(name): self.links.append(attrs[name])
        if tag=='img' and 'alt' not in attrs: raise ValueError('image missing alt text')


def build():
    metrics = public_learning_metrics()
    pages={}
    for path in DOCS.glob('*.html'):
        parsed=Links();parsed.feed(path.read_text());pages[path.resolve()]=parsed
    for page, parsed in pages.items():
        for link in parsed.links:
            ref=urlsplit(link)
            if ref.scheme or ref.netloc: continue
            target=(page.parent / unquote(ref.path)).resolve() if ref.path else page
            if not target.is_relative_to(DOCS): raise ValueError(f'private/outside link: {link}')
            if not target.exists(): raise ValueError(f'broken link in {page.name}: {link}')
            if ref.fragment and target in pages and unquote(ref.fragment) not in pages[target].ids:
                raise ValueError(f'missing anchor in {page.name}: {link}')
    for css in (DOCS/'assets').glob('*.css'):
        for link in re.findall(r'url\([\'\"]?([^\)\'\"]+)', css.read_text()):
            if not urlsplit(link).scheme and not (css.parent/link).exists():
                raise ValueError(f'broken CSS asset: {link}')
    staging=Path(tempfile.mkdtemp(prefix='veda-public-build-'))
    for path in pages:
        content = path.read_text(encoding='utf-8')
        (staging/path.name).write_text(_public_html(content, metrics), encoding='utf-8')
    shutil.copytree(DOCS/'assets',staging/'assets',ignore=shutil.ignore_patterns('.DS_Store','.gitkeep'))
    for file in staging.rglob('*'):
        if file.suffix in ('.sqlite','.sqlite3','.db'): raise ValueError('private database in public assets')
    dest=ROOT/'dist'
    if dest.exists():
        previous=Path(tempfile.mkdtemp(prefix='veda-previous-build-'))/'dist'
        shutil.move(str(dest),str(previous))
    shutil.move(str(staging),str(dest))
    print(f'Validated {len(pages)} HTML pages and local asset links; static build: {dest}')


if __name__=='__main__': build()
