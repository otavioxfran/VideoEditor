import json

from video_editor.finishing import library, suggest


def _transcript_segments():
    return [dict(start=1, end=3, text='Jensen falou', words=[
        dict(word='Jensen', start=1, end=1.5),
        dict(word='falou', start=1.5, end=2.0),
    ]), dict(start=5, end=7, text='Jensen voltou', words=[
        dict(word='Jensen', start=5, end=5.5),
        dict(word='voltou', start=5.5, end=6.0),
    ])]


def test_library_expand_writes_auto_asset_with_credit(jobs, ready, tmp_path):
    job = ready(duration=20, segments=_transcript_segments())
    screen = jobs.path(job['job_id'], 'visual/screen_terms.json')
    screen.parent.mkdir(parents=True, exist_ok=True)
    screen.write_text(json.dumps({'terms': ['NVIDIA']}), encoding='utf-8')
    calls = []

    def request(url):
        calls.append(url)
        if 'api.php' in url:
            return json.dumps({'query': {'pages': {'1': {
                    'title': 'File:NVIDIA logo.png', 'index': 1,
                    'imageinfo': [{
                        'width': 1200, 'thumburl': 'https://example.test/nvidia.png',
                        'descriptionurl': 'https://example.test/nvidia',
                    'extmetadata': {
                        'LicenseShortName': {'value': 'CC BY 4.0'},
                        'Artist': {'value': 'Fotógrafo'},
                    },
                }],
            }}}}).encode()
        return b'image-bytes'

    path = tmp_path / 'library.json'
    value = library.expand(
        jobs, job['job_id'], library_path=path, out_dir=tmp_path / 'auto',
        request=request,
    )
    assert value['downloaded']
    asset = value['downloaded'][0]
    assert asset['auto'] is True and asset['license'] == 'CC BY 4.0'
    assert 'Fotógrafo' in asset['credit'] and 'CC BY 4.0' in asset['credit']
    assert asset['mode'] == 'badge' and asset['source_url']
    assert suggest.load_library(path)['assets'][0]['auto'] is True


def test_library_expand_keeps_people_pending_and_drops_loose_names(jobs, ready, tmp_path):
    job = ready(duration=20, segments=_transcript_segments())
    screen = jobs.path(job['job_id'], 'visual/screen_terms.json')
    screen.parent.mkdir(parents=True, exist_ok=True)
    screen.write_text(json.dumps({'terms': [
        'NVIDIA', 'Lori Huang', 'Lori', 'Alice Huang', 'Alice',
        'Jen-Hsun', 'Jen-Hsun Jensen Huang',
    ]}), encoding='utf-8')
    value = library.expand(
        jobs, job['job_id'], library_path=tmp_path / 'library.json',
        out_dir=tmp_path / 'auto', dry_run=True,
    )
    automatic = {item['term'] for item in value['automatic']}
    pending = {item['term'] for item in value['pending']}
    assert 'NVIDIA' in automatic
    assert {'Lori Huang', 'Alice Huang'} <= pending
    assert not ({'Lori', 'Alice', 'Jen-Hsun'} & (automatic | pending))
    assert value['downloaded'] == []


def test_rejecting_auto_card_marks_library_asset(jobs, ready, tmp_path, monkeypatch):
    job = ready(duration=20)
    path = tmp_path / 'library.json'
    path.write_text(json.dumps({'assets': [dict(
        id='auto-jensen-1', terms=['Jensen'], image=str(tmp_path / 'x.png'),
        mode='cutout', kind='pessoa', auto=True, rejected=False, license='CC BY',
    )], 'webcam_rect': None}), encoding='utf-8')
    monkeypatch.setattr(suggest, 'LIBRARY_PATH', path)
    jobs.write(job['job_id'], 'finishing/suggestions.json', dict(
        schema=1, revision=1, items=[dict(
            id='item-auto', kind='overlay', start=1, end=2,
            payload=dict(image=str(tmp_path / 'x.png'), mode='cutout'),
            reason='teste', evidence={}, priority=60, status='pending',
            needs_text=False, asset_id='auto-jensen-1', auto=True, license='CC BY',
        )]
    ))
    value = suggest.decide(jobs, job['job_id'], dict(
        expected_revision=1, item_id='item-auto', action='reject'
    ))
    assert value['items'][0]['status'] == 'rejected'
    saved = json.loads(path.read_text(encoding='utf-8'))['assets'][0]
    assert saved['rejected'] is True
    assert suggest.load_library(path)['assets'] == []


def _screen(jobs, job, terms):
    screen = jobs.path(job['job_id'], 'visual/screen_terms.json')
    screen.parent.mkdir(parents=True, exist_ok=True)
    screen.write_text(json.dumps({'terms': terms}), encoding='utf-8')


def test_library_falls_back_to_openverse_when_commons_has_nothing(jobs, ready, tmp_path):
    job = ready(duration=20, segments=_transcript_segments())
    _screen(jobs, job, ['Megaspeed'])

    def request(url):
        if 'api.php' in url:
            return json.dumps({'query': {'pages': {}}}).encode()
        if 'openverse' in url:
            assert 'license_type=commercial' in url
            return json.dumps({'results': [{
                'title': 'Megaspeed datacenter', 'url': 'https://example.test/m.jpg',
                'width': 1600, 'license': 'by', 'license_version': '4.0',
                'creator': 'Autora', 'foreign_landing_url': 'https://example.test/m',
            }]}).encode()
        return b'image-bytes'

    path = tmp_path / 'library.json'
    value = library.expand(jobs, job['job_id'], library_path=path, out_dir=tmp_path / 'auto', request=request)
    asset = value['downloaded'][0]
    assert asset['source'] == 'Openverse'
    assert asset['license'] == 'CC BY 4.0'
    assert 'Autora' in asset['credit']


def test_library_remembers_misses_and_survives_network_errors(jobs, ready, tmp_path):
    job = ready(duration=20, segments=_transcript_segments())
    _screen(jobs, job, ['Megaspeed'])
    calls = []

    def empty(url):
        calls.append(url)
        if 'api.php' in url:
            return json.dumps({'query': {'pages': {}}}).encode()
        return json.dumps({'results': []}).encode()

    path = tmp_path / 'library.json'
    first = library.expand(jobs, job['job_id'], library_path=path, out_dir=tmp_path / 'auto', request=empty)
    assert first['missing'] == ['Megaspeed']
    searched = len(calls)
    second = library.expand(jobs, job['job_id'], library_path=path, out_dir=tmp_path / 'auto', request=empty)
    assert second['candidates'] == [] and len(calls) == searched
    # The miss is not an asset: loading the library must not choke on it.
    assert suggest.load_library(path)['assets'] == []

    def offline(url):
        raise OSError('sem rede')

    job2 = ready(duration=20, segments=_transcript_segments())
    _screen(jobs, job2, ['Pequim'])
    third = library.expand(jobs, job2['job_id'], library_path=path, out_dir=tmp_path / 'auto', request=offline)
    assert third['downloaded'] == [] and third['missing'] == ['Pequim'] or third['failed'] == ['Pequim']
