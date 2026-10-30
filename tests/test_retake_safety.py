import pytest
from video_editor.cuts import find_candidates
from video_editor.cut_policy import apply_policy
from video_editor.transcription import normalize
from video_editor.activity import silence_candidates, detect


def transcript_of(texts):
    return normalize([dict(start=i * 3, end=i * 3 + 2, text=text,
        words=[dict(word=word, start=i * 3 + j * (2/len(text.split())),
                    end=i * 3 + (j+1)*(2/len(text.split())), probability=.99)
               for j, word in enumerate(text.split())]) for i, text in enumerate(texts)], {})


@pytest.mark.parametrize('a,b', [
    ('O relatório informa o valor de 100 mil reais', 'O relatório informa o valor de 200 mil reais'),
    ('O relatório informa que ele não recebeu o pagamento', 'O relatório informa que ele recebeu o pagamento'),
    ('Eu acho que ele morreu', 'Eu acho que ele pagou tudo hoje'),
    ('Isso precisa acabar agora mesmo.', 'Isso precisa acabar agora mesmo.'),
    ('Esse exemplo mostra como funciona.', 'Esse exemplo mostra como funciona no cotidiano.'),
    ('O prazo acaba em setembro de 2026', 'O prazo acaba em outubro de 2026'),
    ('Paulo recebeu o pagamento ontem', 'Maria recebeu o pagamento ontem'),
])
def test_ambiguous_candidates_actually_found_but_never_cut(a, b):
    transcript = transcript_of([a, b])
    candidates = find_candidates(transcript)
    assert candidates, 'Negative test must exercise a candidate, not a vacuous loop.'
    assert not any(c['auto'] for c in apply_policy(transcript, candidates, 10))


def test_reset_in_other_statement_does_not_discard_previous():
    transcript = transcript_of(['Essa prova encerra nosso argumento.', 'Vou repetir e explicar o próximo ponto.'])
    candidates = find_candidates(transcript)
    assert candidates
    assert not any(c['auto'] for c in apply_policy(transcript, candidates, 10))


def test_isolated_production_command_has_word_evidence():
    transcript = transcript_of(['Vou repetir', 'Esse é o conteúdo que será preservado.'])
    candidates = apply_policy(transcript, find_candidates(transcript), 10)
    assert len([c for c in candidates if c['auto']]) == 1
    transcript['segments'][0]['words'] = []
    assert not any(c['auto'] for c in apply_policy(transcript, find_candidates(transcript), 10))


def test_intra_segment_repeat_is_review_only():
    transcript = transcript_of(['Eu acho eu acho que podemos seguir.'])
    candidates = find_candidates(transcript)
    assert any(c['id'].startswith('intra-') for c in candidates)
    assert not any(c['auto'] for c in apply_policy(transcript, candidates, 10))


def test_acoustic_proof_and_word_overlap(jobs, ready):
    job = ready()
    transcript = jobs.read(job['job_id'], 'transcript.json')
    assert not silence_candidates(transcript, [], 20)
    assert not silence_candidates(transcript, [dict(start=1, end=10)], 20)
    assert not silence_candidates(transcript, [dict(start=2.1, end=14.9)], 20)
    assert not silence_candidates(transcript, [dict(start=2.1, end=14.9, vad_clear=False)], 20)
    assert not silence_candidates(transcript,[dict(start=2.1,end=14.9,vad_clear=True)],20)[0]['auto']
    result = silence_candidates(transcript, [dict(start=2.1, end=14.9, vad_clear=True,strict_clear=True,peak_db=-80,rms_db=-90)], 20)
    assert result and result[0]['auto']
    assert result[0]['end'] - result[0]['start'] == pytest.approx(12.15)


def test_quiet_music_energy_cannot_authorize_silence_cut(jobs,ready):
    job=ready();transcript=jobs.read(job['job_id'],'transcript.json')
    result=silence_candidates(transcript,[dict(start=2.1,end=14.9,vad_clear=True,peak_db=-46,rms_db=-49)],20)
    assert result and not result[0]['auto']
