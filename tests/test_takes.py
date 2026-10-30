import pytest

from video_editor import takes


def _segment(sid, text, start, step=.3, probability=.95):
    words = []
    for i, token in enumerate(text.split()):
        words.append(dict(id=f'{sid}-w{i + 1}', text=token, start=round(start + i * step, 3),
                          end=round(start + i * step + step * .8, 3), probability=probability))
    return dict(id=sid, start=words[0]['start'], end=words[-1]['end'], text=text, words=words)


def _transcript(*segments):
    return dict(segments=list(segments))


def _texts(transcript, ids):
    words = {w['id']: w['text'] for s in transcript['segments'] for w in s['words']}
    return ' '.join(words[i] for i in ids)


def test_literal_stumble_keeps_second_try():
    t = _transcript(_segment('s1', 'são problemas mas são problemas mas são problemas que foram', 0))
    found = takes.stumbles(t)
    assert len(found) == 1 and found[0]['auto']
    assert _texts(t, found[0]['removed_word_ids']) == 'são problemas mas'
    assert found[0]['end'] < t['segments'][0]['words'][3]['start']  # never touches the kept try


def test_refrain_and_two_word_repeats_are_never_automatic():
    t = _transcript(_segment('s1', 'dá um like dá um like João dá um like dá um like', 0),
                    _segment('s2', 'que absurdo que absurdo que absurdo', 10),
                    _segment('s3', 'é isso é isso', 20))
    assert not any(s['auto'] for s in takes.stumbles(t))


def test_stumble_spans_segments_but_not_long_stops():
    t = _transcript(_segment('s1', 'o Ryan Santos', 0), _segment('s2', 'o Ryan Santos foi lá', 2.5))
    assert [s['auto'] for s in takes.stumbles(t)] == [True]
    far = _transcript(_segment('s1', 'o Ryan Santos', 0), _segment('s2', 'o Ryan Santos foi lá', 10))
    assert takes.stumbles(far) == []


def test_complete_retake_after_pause_removes_earlier_take_and_cue():
    t = _transcript(_segment('s1', 'Ninguém mais quer ficar rico devagar, você tem 20 anos', 0),
                    _segment('s2', 'não, pera', 8),
                    _segment('s3', 'Ninguém mais quer ficar rico devagar, mano, você tem 20 anos', 30))
    found = takes.repeated_takes(t)
    assert len(found) == 1 and found[0]['kind'] == 'repeated_take' and found[0]['auto']
    assert set(found[0]['removed_word_ids']) == {w['id'] for s in t['segments'][:2] for w in s['words']}
    assert found[0]['end'] < t['segments'][2]['words'][0]['start']


def test_lost_number_negation_or_content_between_is_only_a_suggestion():
    number = _transcript(_segment('s1', 'o Brasil cresce 3 por cento ao ano desde 1980', 0),
                         _segment('s2', 'o Brasil cresce por cento ao ano desde sempre', 5))
    negation = _transcript(_segment('s1', 'a gente não trabalha pouco no Brasil hoje', 0),
                           _segment('s2', 'a gente trabalha pouco no Brasil hoje mesmo', 5))
    between = _transcript(_segment('s1', 'o trabalhador brasileiro produz um quarto do americano', 0),
                          _segment('s2', 'isso aparece em todos os estudos sérios de economia', 5),
                          _segment('s3', 'o trabalhador brasileiro produz um quarto do americano hoje', 10))
    for t in (number, negation, between):
        assert not any(c['auto'] for c in takes.repeated_takes(t))
    idea = takes.repeated_takes(between)
    assert idea and idea[0]['kind'] == 'repeated_idea'


def test_partial_repeat_suggests_dropping_the_partial_one():
    t = _transcript(_segment('s1', 'São Bernardo, Santo André, Diadema e Mauá.', 0),
                    _segment('s2', 'São Bernardo, Santo André, Diadema', 4))
    found = takes.repeated_takes(t)
    assert found and not found[0]['auto'] and found[0]['replacement_segment_ids'] == ['s1']
    assert set(found[0]['removed_word_ids']) == {w['id'] for w in t['segments'][1]['words']}


def test_abandoned_later_take_is_not_the_keeper():
    t = _transcript(_segment('s1', 'o nosso concorrente que paga melhor o funcionário', 0),
                    _segment('s2', 'o nosso concorrente que paga melhor o-', 4))
    assert not any(c['auto'] for c in takes.repeated_takes(t))


def test_guard_and_merge():
    t = _transcript(_segment('s1', 'são problemas mas são problemas mas são problemas', 0))
    stumble = takes.stumbles(t)[0]
    other = dict(id='x', kind='retake', auto=True, start=stumble['end'], end=stumble['end'] + 1)
    takes.guard([stumble, other], t)
    assert stumble['auto'] is False  # the kept try would also be cut
    old = dict(id='intra-s1-0-3', kind='retake', auto=False, start=stumble['start'] + .01,
               end=stumble['end'] - .01, removed_word_ids=stumble['removed_word_ids'])
    assert takes.merge([old], [stumble]) == [stumble]


def test_long_sentence_tried_three_times_is_retakes_not_a_refrain():
    t = _transcript(_segment('s1', 'ou seja o iFood ele vai ou seja o iFood ele vai ou seja o iFood ele vai cobrar', 0))
    found = takes.stumbles(t)
    assert found and found[0]['auto']


def _three_takes():
    return _transcript(
        _segment('s1', 'ninguem mais quer ficar rico devagar mano', 0),
        _segment('s2', 'voce tem 20 anos voce tem que estar rico', 4),
        _segment('s3', 'quero os homens altos', 8),
        _segment('s4', 'ninguem mais quer ficar rico devagar mano', 12),
        _segment('s5', 'voce tem 20 anos voce tem que ter pele perfeita', 16),
        _segment('s6', 'e isso calma', 20),
        _segment('s7', 'ninguem mais quer ficar rico devagar irmao', 40),
        _segment('s8', 'voce tem 20 anos voce tem que ser alto', 44),
        _segment('s9', 'acima da media multimilionario', 48),
    )


def test_multisentence_take_blocks_keep_last_and_are_review_only():
    t = _three_takes()
    blocks = takes.repeated_blocks(t)
    assert len(blocks) == 2
    assert all(block['kind'] == 'repeated_block' and block['auto'] is False for block in blocks)
    assert blocks[0]['replacement_segment_ids'] == ['s7', 's8', 's9']
    assert blocks[0]['start'] == 0
    assert blocks[0]['end'] < 12
    assert blocks[1]['start'] == pytest.approx(blocks[0]['end'])
    assert blocks[1]['end'] < 40
    assert 'pele' in blocks[1]['detail']['lost']
    assert 'não diz' in blocks[1]['reason']


def test_take_blocks_need_the_same_opening():
    t = _transcript(_segment('s1', 'primeira coisa completamente diferente', 0),
                    _segment('s2', 'outra coisa sem rela??o nenhuma', 10))
    assert takes.repeated_blocks(t) == []


def test_candidates_do_not_keep_the_single_line_idea_inside_a_block():
    t = _three_takes()
    blocks = takes.repeated_blocks(t)
    suggestions = takes.candidates(t)
    assert {block['id'] for block in blocks} <= {suggestion['id'] for suggestion in suggestions}
    assert not [
        suggestion for suggestion in suggestions
        if suggestion['kind'] == 'repeated_idea'
        and any(_overlap(suggestion, block) for block in blocks)
    ]


def _overlap(a, b):
    return a['start'] < b['end'] and a['end'] > b['start']
