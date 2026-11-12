"""Local ASR; no semantic providers or resource-manager imports."""
import gc
import time
import threading
from video_editor.timeline import digest, interval

_model_lock = threading.RLock()
_cached_model = None
_cached_key = None


def release_model():
    global _cached_model, _cached_key
    with _model_lock:
        _cached_model = _cached_key = None
        gc.collect()


def _model(actual):
    global _cached_model, _cached_key
    from faster_whisper import WhisperModel
    key = (WhisperModel, actual['model'], actual['device'], actual['compute_type'])
    reused = key == _cached_key and _cached_model is not None
    tick = time.monotonic()
    if not reused:
        release_model()
        _cached_model = WhisperModel(actual['model'], device=actual['device'],
                                    compute_type=actual['compute_type'], local_files_only=True)
        _cached_key = key
    actual.update(model_reused=reused, model_load_seconds=time.monotonic() - tick)
    return _cached_model


def _serialize(segments):
    return [dict(start=s.start, end=s.end, text=s.text.strip(), words=[dict(
        word=w.word, start=w.start, end=w.end, probability=w.probability) for w in (s.words or [])
        if w.end > w.start]) for s in segments]


def normalize(segments, source, allow_imprecise=False):
    output = []
    for index, segment in enumerate(segments):
        sid = str(segment.get('id', 's' + str(index + 1)))
        words = [dict(id=sid + '-w' + str(i + 1), text=w.get('word', w.get('text', '')).strip(),
                      start=float(w['start']), end=float(w['end']), probability=float(w.get('probability', 1)))
                 for i, w in enumerate(segment.get('words', []))]
        start, end = float(segment['start']), float(segment['end'])
        if not start < end or start < 0:
            raise ValueError("Segmento ASR inválido.")
        previous = start
        try:
            for word in words:
                interval(word['start'], word['end'], end)
                if word['start'] < previous - .02:
                    raise ValueError("Palavras ASR fora de ordem.")
                previous = word['end']
        except ValueError:
            if not allow_imprecise:
                raise
            # Legacy ASR has zero-duration words. Preserve sentence text and use segment
            # timestamps instead of fabricating precision or silently losing those words.
            words = []
        output.append(dict(id=sid, start=start, end=end, text=segment['text'], words=words))
    if len({s['id'] for s in output}) != len(output):
        raise ValueError("IDs de transcrição duplicados.")
    return dict(schema=1, source_hash=digest(source), segments=output)


def transcribe(path, config, source, hotwords=None):
    # The queue owns GPU inference; the lock also protects direct Python callers.
    with _model_lock:
        return _transcribe(path, config, source, hotwords)


def _transcribe(path, config, source, hotwords=None):
    actual = dict(config)
    if actual['device'] == 'cpu':
        actual['compute_type'] = 'int8'
        actual['batch_size']=1
    def execute():
        model = _model(actual)
        tick = time.monotonic()
        batch = actual.get('batch_size', 1)
        engine = model
        extra = {}
        if batch > 1 and actual['device'] == 'cuda' and hasattr(model, 'model'):
            from faster_whisper import BatchedInferencePipeline
            engine = BatchedInferencePipeline(model=model)
            extra['batch_size'] = batch
        segments, _ = engine.transcribe(
            str(path),
            language=actual['language'],
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={'min_silence_duration_ms': 400},
            **extra,
            **({'hotwords': hotwords} if hotwords else {}),
        )
        result = _serialize(segments)
        actual['transcribe_seconds'] = time.monotonic() - tick
        return result

    try:
        try:
            while True:
                try:
                    segments=execute()
                    break
                except RuntimeError as error:
                    if (actual['device']=='cuda' and 'out of memory' in str(error).lower() and
                        actual.get('batch_size',1)>1):
                        actual['batch_size']//=2
                        actual['batch_fallback']='GPU sem memória; lote reduzido.'
                        continue
                    raise
        except RuntimeError as error:
            gpu_error = any(term in str(error).lower() for term in
                            ('cuda', 'cublas', 'cudnn', 'out of memory', 'compute type'))
            if actual['device'] != 'cuda' or not config['cpu_fallback'] or not gpu_error:
                release_model()
                raise
            release_model()
            actual.update(device='cpu', compute_type='int8',batch_size=1, fallback_reason=str(error))
            segments = execute()
        transcript = normalize(segments, source)
        if config.get('refine_uncertain') and actual['model'] == 'small':
            transcript, refinement = refine(path, transcript, actual, hotwords=hotwords)
            actual['refinement'] = refinement
        return transcript, actual
    except Exception:
        release_model()
        raise


def refine(path, transcript, actual, segment_ids=None, hotwords=None):
    """Only revisit low-confidence short sentences; unavailable medium stays explicit."""
    from copy import deepcopy
    uncertain = [s for s in transcript['segments'] if (segment_ids is None or s['id'] in segment_ids)
                 and s['words'] and s['end'] - s['start'] <= 30
                 and sum(w['probability'] < .6 for w in s['words']) >= max(1, len(s['words']) // 3)]
    if segment_ids is None:
        uncertain = uncertain[:20]
    if not uncertain:
        return transcript, dict(state='not_needed', revisited=0)
    medium = dict(actual, model='medium', batch_size=1)
    try:
        from faster_whisper.utils import download_model
        # Check cached weights before evicting the warm small model. No network download.
        download_model('medium',local_files_only=True)
        model = _model(medium)
    except Exception:
        return transcript, dict(state='unavailable', revisited=0, uncertain_ids=[s['id'] for s in uncertain])
    import wave
    import numpy as np
    with wave.open(str(path), 'rb') as wav:
        audio = np.frombuffer(wav.readframes(wav.getnframes()), dtype='<i2').astype(np.float32) / 32768
    updated = deepcopy(transcript)
    replaced = []
    for segment in uncertain:
        position=next(i for i,s in enumerate(updated['segments']) if s['id']==segment['id'])
        previous=updated['segments'][position-1]['end'] if position else 0
        following=updated['segments'][position+1]['start'] if position+1<len(updated['segments']) else len(audio)/16000
        left,right=max(0,previous,segment['start']-.15),min(len(audio)/16000,following,segment['end']+.15)
        if right<=left:continue
        try:
            pieces, _ = model.transcribe(audio[int(left*16000):int(right*16000)],
                                        language=actual['language'], word_timestamps=True, vad_filter=False,
                                        **({'hotwords': hotwords} if hotwords else {}))
            recognized = _serialize(pieces)
            from video_editor.cuts import tokens
            old=tokens(segment['text']);new=tokens(' '.join(s['text'] for s in recognized))
            anchors=lambda values:[t for t in values if any(c.isdigit() for c in t) or t in ('nao','nunca','jamais','sem')]
            if anchors(old)!=anchors(new):
                continue
            words = [dict(w, start=w['start']+left, end=w['end']+left) for s in recognized for w in s['words']]
            if not words or any(w['start'] < left or w['end'] > right for w in words):
                continue
            if sum(w['probability'] for w in words)/len(words) <= sum(w['probability'] for w in segment['words'])/len(segment['words']):
                continue
            value = normalize([dict(segment,start=max(left,min(segment['start'],words[0]['start'])),
                end=min(right,max(segment['end'],words[-1]['end'])),
                text=' '.join(s['text'] for s in recognized), words=words)], {})['segments'][0]
            updated['segments'][position] = value
            replaced.append(segment['id'])
        except (RuntimeError, ValueError):
            continue
    return updated, dict(state='complete', revisited=len(uncertain), replaced_ids=replaced)
