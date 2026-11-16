"""Explicit, license-filtered image downloads from Wikimedia Commons."""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = 'https://commons.wikimedia.org/w/api.php'
USER_AGENT = 'video-editor-finishing/1.0 (local editing)'
FREE = re.compile(r'(cc[- ]by|cc0|public domain|pd|attribution)', re.I)
MIN_WIDTH = 600


def license_ok(name, width):
    text = str(name or '')
    if re.search(r'\b(nc|nd)\b|non-?commercial|no-?deriv', text, re.I):
        return False
    return bool(FREE.search(text)) and int(width or 0) >= MIN_WIDTH


def slug(text):
    value = re.sub(r'[^a-z0-9]+', '-', str(text).casefold()).strip('-')
    return value or 'image'


def _get(url, request=None):
    if request is not None:
        return request(url)
    for attempt in range(6):
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers={'User-Agent': USER_AGENT}),
                timeout=30,
            ) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code != 429:
                raise
            time.sleep(10 * (attempt + 1))
    raise RuntimeError('Wikimedia Commons limitou as requisições (429).')


def search(query, limit=10, request=None):
    params = dict(
        action='query', format='json', generator='search',
        gsrsearch=f'filetype:bitmap {query}', gsrnamespace=6, gsrlimit=limit,
        prop='imageinfo', iiprop='url|extmetadata|size', iiurlwidth=1600,
    )
    data = json.loads(
        _get(API + '?' + urllib.parse.urlencode(params), request=request)
    )
    pages = data.get('query', {}).get('pages', {}).values()
    return sorted(pages, key=lambda page: page.get('index', 99))


def fetch_images(queries, out_dir, request=None):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    credits_path = out / 'creditos.txt'
    existing = []
    if credits_path.is_file():
        existing = [line for line in credits_path.read_text(encoding='utf-8').splitlines() if line]
    records = []
    for query in queries:
        for page in search(query, request=request):
            info = (page.get('imageinfo') or [{}])[0]
            meta = info.get('extmetadata') or {}
            license_name = meta.get('LicenseShortName', {}).get('value', '')
            width = info.get('width') or 0
            if not license_ok(license_name, width):
                continue
            url = info.get('thumburl') or info.get('url')
            if not url:
                continue
            suffix = Path(urllib.parse.urlparse(url).path).suffix.lower() or '.jpg'
            stem = slug(query)
            path = out / f'{stem}{suffix}'
            counter = 2
            while path.exists():
                path = out / f'{stem}-{counter}{suffix}'
                counter += 1
            path.write_bytes(_get(url, request=request))
            artist = re.sub(
                '<[^>]+>', '', meta.get('Artist', {}).get('value', '')
            ).strip()
            title = str(page.get('title', '')).removeprefix('File:')
            credit = (
                f'{title}: {artist or "autor desconhecido"}, {license_name}, '
                f'{info.get("descriptionurl", "")}'
            )
            if credit not in existing:
                existing.append(credit)
            records.append(
                dict(
                    query=query, path=str(path), title=title, artist=artist,
                    license=license_name, url=info.get('descriptionurl', ''),
                )
            )
            break
    credits_path.write_text('\n'.join(existing) + ('\n' if existing else ''), encoding='utf-8')
    return records
