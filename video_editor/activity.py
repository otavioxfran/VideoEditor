"""Acoustic evidence with VAD and conservative energy gates, never ASR gaps."""
import re
from video_editor.media import binary


def _silences(path, duration, threshold, origin=0, minimum=2.5):
    # silencedetect emits its measurements to stderr, so collect it explicitly.
    import subprocess
    seek=['-ss',f'{origin:.9f}'] if origin>0 else []
    result = subprocess.run([binary('ffmpeg'), '-nostdin', '-hide_banner', *seek, '-i', str(path), '-vn', '-sn', '-dn', '-af',
                             f'aresample=async=1:first_pts=0,silencedetect=noise={threshold}dB:d={minimum}', '-f', 'null', '-'],
                            capture_output=True, text=True, encoding='utf-8', errors='replace')
    if result.returncode:
        raise RuntimeError(result.stderr[-3000:] or f'Detecção acústica interrompida (código {result.returncode}).')
    start, silence = None, []
    for event, value in re.findall(r'silence_(start|end):\s*([\d.]+)', result.stderr):
        time = float(value)
        if event == 'start':
            start = max(0, time)
        elif start is not None:
            silence.append(dict(start=start, end=min(time, duration)))
            start = None
    if start is not None and duration > start:
        silence.append(dict(start=start, end=duration))
    return silence


def detect(path, duration, source=None, origin=0):
    # Measure the raw's channels together: a mono downmix can cancel opposite-phase
    # music and must never serve as the only proof of acoustic silence.
    acoustic_source=source or path
    silence=_silences(acoustic_source,duration,-40,origin)
    if silence:
        strict=_silences(acoustic_source,duration,-65,origin)
        # Independent local Silero VAD protects quiet/untranscribed speech as well.
        # The model ships with faster-whisper; this does not call a remote provider.
        import wave
        import numpy as np
        from faster_whisper.vad import get_speech_timestamps, VadOptions
        with wave.open(str(path), 'rb') as wav:
            if wav.getframerate() != 16000 or wav.getnchannels() != 1 or wav.getsampwidth() != 2:
                raise ValueError('VAD requer WAV PCM mono 16 kHz.')
            audio = np.frombuffer(wav.readframes(wav.getnframes()), dtype='<i2').astype(np.float32) / 32768
        speech = get_speech_timestamps(audio, VadOptions(threshold=.3, min_speech_duration_ms=0,
                                                      min_silence_duration_ms=400, speech_pad_ms=220))
        for item in silence:
            item['vad_clear'] = not any(s['start'] / 16000 < item['end'] and
                                        s['end'] / 16000 > item['start'] for s in speech)
            item['strict_clear']=any(s['start']<=item['start']+.325 and s['end']>=item['end']-.325 for s in strict)
            samples=audio[int(item['start']*16000):int(item['end']*16000)]
            if len(samples):
                item['peak_db']=float(20*np.log10(max(float(np.max(np.abs(samples))),1e-6)))
                item['rms_db']=float(20*np.log10(max(float(np.sqrt(np.mean(samples*samples))),1e-6)))
    return silence


def silence_candidates(transcript, silences, duration):
    # Require speech on BOTH sides, all overlapping ASR words excluded, conservative margins.
    words = [w for s in transcript['segments'] for w in s['words']]
    output = []
    for acoustic in silences:
        start, end = acoustic['start'], acoustic['end']
        if end - start <= 2.5 or not 0 <= start < end <= duration:
            continue
        if acoustic.get('vad_clear') is not True:
            continue
        if any(w['start'] < end and w['end'] > start for w in words):
            continue
        before = [w for w in words if w['end'] <= start]
        after = [w for w in words if w['start'] >= end]
        if not before or not after:
            continue
        left, right = max(start + .325, max(w['end'] for w in before) + .22), min(
            end - .325, min(w['start'] for w in after) - .16)
        if right <= left:
            continue
        # -40 dB alone can include quiet music or relevant sounds. Keep those review-only;
        # absent energy measurements are not authorization either.
        automatic=(acoustic.get('strict_clear') is True and acoustic.get('rms_db',0)<=-65 and acoustic.get('peak_db',0)<=-55)
        reason=('Silêncio acústico e VAD confirmados; pausa-alvo de aproximadamente 650 ms.' if automatic else
                'Baixa energia sem fala detectada; pode conter som relevante. Ouça antes de comprimir.')
        output.append(dict(id='silence-' + str(len(output) + 1), start=left, end=right,
                           kind='silence', auto=automatic, reason=reason,
                           evidence=dict(acoustic=acoustic, threshold_db=-40, vad='silero_local', speech_margins=[.22, .16])))
    return output


def adaptive_candidates(transcript,path,native_audio,duration):
    """Noise-floor proposals with independent native-channel proof; human-gated by caller."""
    import wave
    import numpy as np
    from faster_whisper.vad import get_speech_timestamps,VadOptions
    with wave.open(str(path),'rb') as wav:
        if (wav.getframerate(),wav.getnchannels(),wav.getsampwidth())!=(16000,1,2):
            raise ValueError('Análise de ritmo requer PCM mono 16 kHz.')
        audio=np.frombuffer(wav.readframes(wav.getnframes()),dtype='<i2').astype(np.float32)/32768
    block=320
    if len(audio)<block:return []
    frames=audio[:len(audio)//block*block].reshape(-1,block)
    levels=20*np.log10(np.maximum(np.sqrt(np.mean(frames*frames,axis=1)),1e-6))
    floor=float(np.percentile(levels,15))
    # silencedetect measures sample peaks, whereas the estimated floor is RMS.
    threshold=min(-30,max(-65,floor+15))
    native=_silences(native_audio,duration,threshold,minimum=.9)
    if not native:return []
    speech=get_speech_timestamps(audio,VadOptions(threshold=.3,min_speech_duration_ms=0,
        min_silence_duration_ms=400,speech_pad_ms=220))
    words=[w for s in transcript['segments'] for w in s['words']]
    result=[]
    for span in native:
        start,end=span['start'],span['end']
        if end-start<=.9 or any(w['start']<end-.16 and w['end']>start+.22 for w in words):continue
        before=[w['end'] for w in words if w['end']<=start+.22];after=[w['start'] for w in words if w['start']>=end-.16]
        if not before or not after:continue
        left,right=max(start+.325,max(before)+.22),min(end-.325,min(after)-.16)
        if right-left<.08:continue
        if any(s['start']/16000<right and s['end']/16000>left for s in speech):continue
        samples=audio[int(start*16000):int(min(end,start+1)*16000)]
        if len(samples)<512:continue
        spectrum=np.abs(np.fft.rfft(samples[:512]*np.hanning(512)))**2+1e-16
        flatness=float(np.exp(np.mean(np.log(spectrum)))/np.mean(spectrum))
        local_levels=levels[int(start/.02):int(end/.02)]
        variation=float(np.std(local_levels)) if len(local_levels) else 100
        result.append(dict(id=f'adaptive-{len(result)+1}',start=left,end=right,kind='adaptive_silence',auto=False,
            reason='Pausa sem fala acima do ruído ambiente; proposta para avaliação humana.',
            evidence=dict(acoustic=span,threshold_db=threshold,noise_floor_db=floor,vad_clear=True,
                          native_clear=True,spectral_flatness=flatness,level_variation_db=variation,
                          stationary_noise=flatness>=.35 and variation<=2)))
    return result


def adaptive_for_job(jobs,job_id,transcript,duration):
    from pathlib import Path
    from video_editor import storage
    from video_editor.timeline import digest
    from video_editor.safety import validate_source
    info=jobs.info(job_id)
    wav=jobs.path(job_id,'audio/extracted_16k.wav')
    if not wav.exists() or not info.get('render_audio'):return []
    validate_source(info['render_audio'])
    signature=digest(dict(transcript=digest(transcript),audio=info['render_audio'],duration=duration,
                          engine=digest(Path(__file__).read_text(encoding='utf-8'))))
    path=jobs.path(job_id,'audio/adaptive.json')
    if path.exists():
        existing=storage.read(path)
        if existing['signature']==signature:return existing['suggestions']
    result=adaptive_candidates(transcript,wav,info['render_audio']['path'],duration)
    jobs.write(job_id,'audio/adaptive.json',dict(signature=signature,suggestions=result))
    return result
