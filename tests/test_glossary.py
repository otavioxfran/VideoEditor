
from video_editor import glossary
from video_editor.models import PrepareConfig
from video_editor.timeline import digest


def _transcript(entries):
    words = [
        dict(
            id=f's1-w{index + 1}',
            text=text,
            start=float(index),
            end=float(index + 1),
            probability=probability,
        )
        for index, (text, probability) in enumerate(entries)
    ]
    return dict(
        schema=1,
        segments=[
            dict(
                id='s1',
                start=0.0,
                end=float(len(words)),
                text=' '.join(text for text, _ in entries),
                words=words,
            )
        ],
    )


def test_correct_keeps_ids_times_and_punctuation():
    transcript = _transcript(
        [('Jansen', .9), ('Hwang,', .4), ('disse.', .9)]
    )
    corrected, changes = glossary.correct(transcript, ['Jensen Huang'])
    segment = corrected['segments'][0]
    assert [word['id'] for word in segment['words']] == [
        's1-w1', 's1-w2', 's1-w3'
    ]
    assert [(word['start'], word['end']) for word in segment['words']] == [
        (0.0, 1.0), (1.0, 2.0), (2.0, 3.0)
    ]
    assert [word['text'] for word in segment['words']] == [
        'Jensen', 'Huang,', 'disse.'
    ]
    assert segment['text'] == 'Jensen Huang, disse.'
    assert segment['words'][0]['raw_text'] == 'Jansen'
    assert len(changes) == 2


def test_confident_lowercase_words_are_not_touched():
    transcript = _transcript(
        [('cloud', .95), ('piton', .95), ('piton', .4)]
    )
    corrected, changes = glossary.correct(transcript, ['Cloud', 'Python'])
    words = corrected['segments'][0]['words']
    assert words[0]['text'] == 'cloud'
    assert words[1]['text'] == 'piton'
    assert words[2]['text'] == 'Python'
    assert [(change['before'], change['after']) for change in changes] == [
        ('piton', 'Python')
    ]


def test_correction_is_idempotent():
    transcript = _transcript([('Laurie', .9), ('Hwang', .9)])
    first, changes = glossary.correct(transcript, ['Lori Huang'])
    second, again = glossary.correct(first, ['Lori Huang'])
    assert [word['text'] for word in first['segments'][0]['words']] == [
        'Lori', 'Huang'
    ]
    assert len(changes) == 2
    assert again == []
    assert second == first


def test_hotwords_respects_limit_and_priority():
    terms = ['Zeta Priority'] + [
        f'Term{index:03d} ' + 'x' * 20 for index in range(100)
    ]
    value = glossary.hotwords(terms)
    assert value.startswith('Zeta Priority')
    assert len(value) <= glossary.MAX_HOTWORD_CHARS
    assert len(value.split()) <= glossary.MAX_HOTWORD_TOKENS
    assert 'Term000' in value
    assert 'Term099' not in value
    assert glossary.hotwords(['NVIDIA', 'nvidia', 'CUDA']) == 'NVIDIA, CUDA'


def test_low_probability_does_not_loosen_similarity():
    transcript = _transcript(
        [('Code,', .15), ('Unvidia', .9), ('Hwang,', .9)]
    )
    corrected, changes = glossary.correct(
        transcript, ['CUDA', 'NVIDIA', 'Huang']
    )
    assert [word['text'] for word in corrected['segments'][0]['words']] == [
        'Code,', 'NVIDIA', 'Huang,'
    ]
    assert [(change['before'], change['after']) for change in changes] == [
        ('Unvidia', 'NVIDIA'),
        ('Hwang,', 'Huang,'),
    ]


def test_screen_terms_counts_frames_and_filters_noise(tmp_path, monkeypatch):
    frames = [
        ['Sobre Jensen Huang e NVIDIA', 'Agora CUDA 2024'],
        ['Sobre Jensen Huang e Lori Huang', 'NVIDIA CUDA'],
        ['Sobre Cap Lori Huang', 'Jensen Huang'],
        [
            'RELATORIONVIDIA OGRANDEPROJETODAEMPRESA',
            'Anos Ele Pelos Tras Capítulo Anterior',
            'Grande Pelos Chips',
        ],
    ]
    monkeypatch.setattr(glossary, 'ocr_available', lambda: True)
    monkeypatch.setattr(glossary, '_ocr_lines', lambda *args, **kwargs: frames)
    cache = tmp_path / 'visual' / 'screen_terms.json'
    value = glossary.screen_terms('video.mp4', 30, cache_path=cache)
    assert value['state'] == 'ok'
    assert value['frames'] == 4
    terms = value['terms']
    assert 'Jensen' in terms
    assert 'Huang' in terms
    assert 'Jensen Huang' in terms
    assert 'Lori' in terms
    assert 'Lori Huang' in terms
    assert 'NVIDIA' in terms
    assert 'CUDA' in terms
    for noise in (
        '2024', 'CAP', 'MARKET', 'IA', 'Anos', 'Ele', 'Mulher', 'Pelos',
        'Tras', 'Proximo', 'Capítulo', 'Anterior', 'RELATORIONVIDIA',
        'OGRANDEPROJETODAEMPRESA', 'Grande Pelos Chips', 'Cap Lori Huang',
    ):
        assert noise not in terms
    assert cache.is_file()
    monkeypatch.setattr(
        glossary,
        '_ocr_lines',
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('offline')),
    )
    cached = glossary.screen_terms('video.mp4', 30, cache_path=cache)
    assert cached['terms'] == terms


def test_screen_terms_reports_unavailable_without_rapidocr(monkeypatch):
    monkeypatch.setattr(glossary, 'ocr_available', lambda: False)
    assert glossary.screen_terms('video.mp4', 30) == dict(
        state='unavailable', reason='rapidocr_missing', terms=[]
    )


def test_transcribe_passes_hotwords_only_when_present(monkeypatch):
    from video_editor import transcription

    calls = []

    class Model:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, path, **kwargs):
            calls.append(kwargs)
            return iter([]), None

    monkeypatch.setattr('faster_whisper.WhisperModel', Model)
    transcription.release_model()
    config = PrepareConfig(device='cpu').model_dump()
    transcription.transcribe('unused.wav', config, {}, hotwords='Jensen Huang')
    assert calls[-1]['hotwords'] == 'Jensen Huang'
    transcription.release_model()
    transcription.transcribe('unused.wav', config, {})
    assert 'hotwords' not in calls[-1]
    transcription.release_model()


def test_pipeline_unchanged_without_terms(jobs, ready, monkeypatch):
    from video_editor import pipeline

    job = ready(duration=20)
    job_id = job['job_id']
    jobs.path(job_id, 'transcript.json').unlink()
    calls = []

    def fake(path, config, source, hotwords=None):
        calls.append(hotwords)
        return dict(schema=1, source_hash=digest(source), segments=[]), {}

    monkeypatch.setattr(pipeline.transcription, 'transcribe', fake)
    monkeypatch.setattr(glossary, 'ocr_available', lambda: True)
    monkeypatch.setattr(
        glossary,
        'screen_terms',
        lambda *args, **kwargs: dict(state='ok', terms=[]),
    )
    monkeypatch.setattr(glossary, 'user_terms', lambda: [])
    monkeypatch.setattr(pipeline, 'user_settings', lambda: {})
    transcript, _ = pipeline.Pipeline(jobs)._transcript(
        job_id, jobs.info(job_id), 'unused.wav', digest(job['source']),
        dict(duration=20),
    )
    assert calls == [None]
    assert 'glossary' not in transcript


def test_pipeline_uses_screen_terms_as_hotwords(jobs, ready, monkeypatch):
    from video_editor import pipeline

    job = ready(duration=20)
    job_id = job['job_id']
    jobs.path(job_id, 'transcript.json').unlink()
    calls = []

    def fake(path, config, source, hotwords=None):
        calls.append(hotwords)
        return dict(schema=1, source_hash=digest(source), segments=[]), {}

    monkeypatch.setattr(pipeline.transcription, 'transcribe', fake)
    monkeypatch.setattr(glossary, 'ocr_available', lambda: True)
    monkeypatch.setattr(
        glossary,
        'screen_terms',
        lambda *args, **kwargs: dict(state='ok', terms=['Jensen', 'NVIDIA']),
    )
    monkeypatch.setattr(glossary, 'user_terms', lambda: ['Lori'])
    monkeypatch.setattr(
        pipeline, 'user_settings', lambda: {'glossary_hotwords': True}
    )
    transcript, _ = pipeline.Pipeline(jobs)._transcript(
        job_id, jobs.info(job_id), 'unused.wav', digest(job['source']),
        dict(duration=20),
    )
    assert calls == ['Lori, Jensen, NVIDIA']
    assert transcript['glossary']['hotwords'] == 'Lori, Jensen, NVIDIA'
    assert transcript['glossary']['screen_terms'] == ['Jensen', 'NVIDIA']
    assert transcript['glossary']['hotwords_used'] is True


def test_pipeline_hotwords_are_off_by_default(jobs, ready, monkeypatch):
    from video_editor import pipeline

    job = ready(duration=20)
    job_id = job['job_id']
    jobs.path(job_id, 'transcript.json').unlink()
    calls = []

    def fake(path, config, source, hotwords=None):
        calls.append(hotwords)
        return dict(schema=1, source_hash=digest(source), segments=[]), {}

    monkeypatch.setattr(pipeline.transcription, 'transcribe', fake)
    monkeypatch.setattr(glossary, 'ocr_available', lambda: True)
    monkeypatch.setattr(
        glossary,
        'screen_terms',
        lambda *args, **kwargs: dict(state='ok', terms=['Jensen']),
    )
    monkeypatch.setattr(glossary, 'user_terms', lambda: ['Lori'])
    monkeypatch.setattr(pipeline, 'user_settings', lambda: {})
    transcript, _ = pipeline.Pipeline(jobs)._transcript(
        job_id, jobs.info(job_id), 'unused.wav', digest(job['source']),
        dict(duration=20),
    )
    assert calls == [None]
    assert transcript['glossary']['hotwords_used'] is False
    assert transcript['glossary']['hotwords'] == 'Lori, Jensen'


def test_common_word_never_becomes_term_even_with_low_probability():
    transcript = {'segments': [{'id': 's1', 'text': 'a vida', 'words': [
        {'id': 'w1', 'text': 'a', 'start': 0.0, 'end': 0.1, 'probability': 0.9},
        {'id': 'w2', 'text': 'vida', 'start': 0.1, 'end': 0.4, 'probability': 0.2},
    ]}]}
    updated, changes = glossary.correct(transcript, ['NVIDIA'])
    assert changes == []
    assert updated['segments'][0]['text'] == 'a vida'
