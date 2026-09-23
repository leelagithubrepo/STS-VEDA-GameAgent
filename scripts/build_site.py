#!/usr/bin/env python3
"""Validate the static public site and produce the identical Sites output.

GitHub Pages serves docs/; Sites serves dist/. Private telemetry never enters
this build. This performs file/link checks, not browser or visual testing.
"""
from html.parser import HTMLParser
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import urlsplit, unquote

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / 'docs'


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
    for path in pages: shutil.copy2(path,staging/path.name)
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
