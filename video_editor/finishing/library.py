"""Expand the local finishing library with license-filtered web images."""
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from video_editor import storage
from video_editor.analysis import glossary
from video_editor.finishing import assets, suggest

MAX_DOWNLOADS = 15
COMMON = {suggest._normalize(word) for word in glossary.COMMON_WORDS}
NON_PERSON = {
    'nvidia', 'china', 'megaspeed', 'pequim', 'cuda', 'deepseek', 'qwen',
    'openai', 'google', 'microsoft', 'apple', 'meta', 'amazon',
}


def _auto_dir(library_path):
    return Path(library_path).parent / 'finishing_library.local' / 'auto'


def _tokens(text):
    return tuple(suggest._normalize(text).split())


def _common(token):
    return token in COMMON


def _candidates(jobs, job_id, existing):
    existing_terms = [_tokens(term) for asset in existing for term in asset.get('terms') or []]
    existing_tokens = {token for term in existing_terms for token in term}
    transcript = jobs.read(job_id, 'transcript.json')
    values = Counter()
    for segment in transcript.get('segments') or []:
        words = segment.get('words') or []
        for index, word in enumerate(words):
            text = str(word.get('text') or '').strip('.,;:!?()[]')
            if index == 0 or len(text) <= 2 or not text[:1].isupper():
                continue
            if not _common(_tokens(text)[0] if _tokens(text) else text):
                values[text] += 1
    ocr = []
    screen = jobs.path(job_id, 'visual/screen_terms.json')
    if screen.is_file():
        ocr.extend(storage.read(screen).get('terms') or [])
    user = glossary.user_terms()
    raw = [(term, True) for term in ocr] + [(term, True) for term in user]
    raw += [(term, False) for term, count in values.items() if count >= 2]
    candidates, seen = [], set()
    for term, trusted in raw:
        tokens = _tokens(term)
        if not tokens or all(_common(token) for token in tokens):
            continue
        if len(tokens) == 1 and not trusted:
            continue
        if any(set(term) <= set(tokens) for term in existing_terms):
            continue
        if any(
            len(token) >= 3 and any(existing.startswith(token) and existing != token for existing in existing_tokens)
            for token in tokens
        ):
            continue
        key = ' '.join(tokens)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(dict(
            term=str(term).strip(), tokens=tokens,
            needs_confirmation=len(tokens) >= 2 and not any(
                token in NON_PERSON for token in tokens
            ),
            trusted=trusted,
        ))
    raw_terms = [_tokens(term) for term, _ in raw if len(_tokens(term)) > 1]

    # A one-word name inside a longer name is not a separate candidate.
    result = []
    for candidate in candidates:
        tokens = set(candidate['tokens'])
        if len(tokens) == 1 and any(tokens < set(term) for term in raw_terms):
            continue
        if any(
            tokens < set(other['tokens'])
            for other in candidates if other is not candidate
        ):
            continue
        result.append(candidate)
    return result[:MAX_DOWNLOADS]


def _title_matches(title, query):
    title_tokens = set(_tokens(title))
    return set(_tokens(query)) <= title_tokens


def _credit(title, artist, license_name, url):
    return f'"{title}" por {artist or "autor desconhecido"}, {license_name}, {url}'


def expand(jobs, job_id, library_path=None, out_dir=None, dry_run=False, request=None):
    path = Path(library_path or suggest.LIBRARY_PATH)
    value = storage.read(path) if path.is_file() else dict(assets=[], webcam_rect=None)
    candidates = _candidates(jobs, job_id, value.get('assets') or [])
    pending = [item for item in candidates if item['needs_confirmation']]
    automatic = [item for item in candidates if not item['needs_confirmation']]
    if dry_run:
        return dict(candidates=candidates, automatic=automatic, pending=pending,
                    downloaded=[], dry_run=True)
    target = Path(out_dir) if out_dir else _auto_dir(path)
    target.mkdir(parents=True, exist_ok=True)
    downloaded, missing, failed = [], [], []
    for candidate in automatic:
        try:
            hits = assets.find_free_images(candidate['term'], limit=5, request=request)
        except Exception:
            # A network problem must never break suggestions; try again next time.
            failed.append(candidate['term'])
            continue
        hit = next((item for item in hits if _title_matches(item['title'], candidate['term'])), None)
        if hit is None:
            # Remember the miss so the same name is not searched on every video.
            missing.append(candidate['term'])
            value.setdefault('assets', []).append(dict(
                id=f"missing-{suggest_asset_slug(candidate['term'])}",
                terms=[candidate['term']], image='', auto=True, not_found=True,
                added_at=datetime.now(timezone.utc).isoformat(),
            ))
            continue
        suffix = Path(hit['url'].split('?', 1)[0]).suffix.lower()
        if suffix not in ('.jpg', '.jpeg', '.png', '.webp', '.gif'):
            suffix = '.jpg'
        image = target / f"{suggest_asset_slug(candidate['term'])}{suffix}"
        counter = 2
        while image.exists():
            image = target / f"{suggest_asset_slug(candidate['term'])}-{counter}{suffix}"
            counter += 1
        try:
            image.write_bytes(assets._get(hit['url'], request=request))
        except Exception:
            failed.append(candidate['term'])
            continue
        title = hit['title']
        item = dict(
            id=f"auto-{suggest_asset_slug(candidate['term'])}-{len(downloaded) + 1}",
            terms=[candidate['term']], image=str(image), mode='cover',
            kind='broll', license=hit['license'],
            credit=_credit(title, hit['artist'], hit['license'], hit['source_url']),
            source=hit['source'], source_url=hit['source_url'],
            added_at=datetime.now(timezone.utc).isoformat(),
            auto=True, rejected=False,
        )
        if any(word in title.casefold() for word in ('logo', 'brand', 'icon')):
            item['mode'] = 'badge'
            item['kind'] = 'logo'
        elif any(word in title.casefold() for word in ('person', 'portrait', 'man', 'woman')):
            item['mode'] = 'cutout'
            item['kind'] = 'pessoa'
        value.setdefault('assets', []).append(item)
        downloaded.append(item)
    for candidate in pending:
        value.setdefault('assets', []).append(dict(
            id=f"person-{suggest_asset_slug(candidate['term'])}",
            terms=[candidate['term']], image='', mode='cutout', kind='pessoa',
            auto=True, rejected=False, needs_confirmation=True,
            added_at=datetime.now(timezone.utc).isoformat(),
        ))
    path.parent.mkdir(parents=True, exist_ok=True)
    storage.write(path, value)
    return dict(candidates=candidates, automatic=automatic, pending=pending,
                downloaded=downloaded, missing=missing, failed=failed, dry_run=False)


def suggest_asset_slug(text):
    return re.sub(r'[^a-z0-9]+', '-', str(text).casefold()).strip('-') or 'auto'


def reject(library_path, asset_id):
    path = Path(library_path)
    value = storage.read(path)
    for item in value.get('assets') or []:
        if item.get('id') == asset_id:
            item['rejected'] = True
            break
    storage.write(path, value)
    return value
