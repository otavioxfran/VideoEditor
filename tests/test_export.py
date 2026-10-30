"""Real FFmpeg integration: visual flashes, audio pulses and subtitle timing."""
import math
import shutil
import struct
import subprocess
import wave
from concurrent.futures import ThreadPoolExecutor
import pytest
from video_editor import media
from video_editor.export.renderer import render, DEFAULT_CONFIG
from video_editor.export.service import Exports
from video_editor.export.subtitles import cues
from video_editor.models import PrepareConfig
from video_editor.pipeline import Pipeline
from video_editor.transcription import normalize
from video_editor.worker import Worker

pytestmark = pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='FFmpeg requerido')


@pytest.fixture
def pulse_video(jobs, request):
    pulses = [.5, 2.5, 5]
    audio = jobs.source_dir / 'pulses.wav'
    with wave.open(str(audio),'wb') as handle:
        handle.setparams((1,2,16000,0,'NONE','not compressed'))
        data = bytearray()
        for sample in range(6 * 16000):
            t = sample / 16000
            active = any(p <= t < p+.12 for p in pulses)
            value = int(28000 * math.sin(2*math.pi*700*t)) if active else 0
            data.extend(struct.pack('<h', value))
        handle.writeframes(data)
    source = jobs.source_dir / 'pulses.mp4'
    expression = '+'.join(f'between(t,{p},{p+.12})' for p in pulses)
    media.run(['ffmpeg','-nostdin','-y','-v','error','-f','lavfi','-i','color=black:s=160x90:r=30:d=6',
               '-i',str(audio),'-vf',f"drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='{expression}'",
               '-c:v','libx264','-preset',getattr(request,'param','medium'),'-pix_fmt','yuv420p','-c:a','aac','-t','6',str(source)])
    return source


def _signals(output, directory):
    frames = subprocess.run([media.binary('ffmpeg'),'-v','error','-i',str(output),'-f','rawvideo','-pix_fmt','gray','-'],
                            capture_output=True,check=True).stdout
    stride = 160*90
    on = [sum(frames[i:i+stride])/stride>200 for i in range(0,len(frames),stride)]
    flashes = [i/30 for i,v in enumerate(on) if v and (i==0 or not on[i-1])]
    wav = directory/'decoded.wav'
    media.run(['ffmpeg','-nostdin','-y','-v','error','-i',str(output),'-vn','-ar','16000','-ac','1',str(wav)])
    with wave.open(str(wav),'rb') as handle:
        values = struct.unpack('<'+'h'*handle.getnframes(),handle.readframes(handle.getnframes()))
    blocks = [max(abs(v) for v in values[i:i+160])>8000 for i in range(0,len(values),160)]
    sounds = [i/100 for i,v in enumerate(blocks) if v and (i==0 or not blocks[i-1])]
    return flashes,sounds


@pytest.mark.parametrize('pulse_video,batch_size', [('medium',24),('medium',1),('ultrafast',1)], indirect=['pulse_video'])
def test_real_mp4_srt_sync_and_concurrent_export_requests(jobs, ready, pulse_video, monkeypatch, batch_size):
    monkeypatch.setattr('video_editor.export.renderer.MAX_BATCH_KEEPS',batch_size)
    job = ready('pulses.mp4',duration=6,segments=[dict(start=p,end=p+.12,text=f'Pulso {i}',
               words=[dict(word=f'Pulso {i}',start=p,end=p+.12)]) for i,p in enumerate([.5,2.5,5])])
    jid = job['job_id']
    from video_editor.editing import mutate
    mutate(jobs,jid,dict(expected_revision=0,action='cut',start=1,end=2))
    before = (pulse_video.read_bytes(), pulse_video.stat().st_mtime_ns)
    worker = Worker()
    config = dict(DEFAULT_CONFIG,video_codec='libx264')
    exports = Exports(jobs,worker,config=config)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: exports.request(jid,1),range(2)))
    worker.queue.join()
    worker.close()
    assert results[0]['render_id'] != results[1]['render_id']
    for item in results:
        exported = exports.get(jid,item['render_id'])
        assert exported['state'] == 'complete', exported
        assert exported['status'] == 'current'
        prefix = f'exports/{item["render_id"]}'
        flashes, sounds = _signals(jobs.path(jid,prefix+'/video.mp4'), jobs.path(jid,prefix+'/video.mp4').parent)
        assert len(flashes) == len(sounds) == 3
        assert flashes == pytest.approx([.5,1.5,4],abs=1/30)
        assert sounds == pytest.approx(flashes,abs=1/30)
        snapshot = jobs.read(jid,prefix+'/snapshot.json')
        assert [c['start'] for c in cues(snapshot['transcript'],snapshot['timeline'])] == pytest.approx(flashes,abs=.1)
    assert (pulse_video.read_bytes(), pulse_video.stat().st_mtime_ns) == before


def test_filtergraph_does_not_go_in_windows_command_line(jobs, ready, pulse_video):
    job = ready('pulses.mp4',duration=6)
    from video_editor.editing import mutate
    for revision in range(8):
        mutate(jobs,job['job_id'],dict(expected_revision=revision,action='cut',start=.2+revision*.5,end=.267+revision*.5))
    worker = Worker()
    exports = Exports(jobs,worker,config=dict(DEFAULT_CONFIG,video_codec='libx264'))
    result = exports.request(job['job_id'],8)
    worker.queue.join();worker.close()
    assert exports.get(job['job_id'],result['render_id'])['state'] == 'complete'
    assert jobs.path(job['job_id'],f'exports/{result["render_id"]}/filtergraph.txt').exists()


def test_preparation_checkpoints_and_real_acoustic_detection(jobs,pulse_video,monkeypatch):
    job = jobs.create(pulse_video,PrepareConfig(device='cpu').model_dump())
    calls=[]
    def asr(path,config,source):
        calls.append(path)
        return normalize([dict(start=.5,end=.62,text='Um.',words=[dict(word='Um.',start=.5,end=.62)]),
                          dict(start=5,end=5.12,text='Dois.',words=[dict(word='Dois.',start=5,end=5.12)])],source),config
    monkeypatch.setattr('video_editor.transcription.transcribe',asr)
    before = (pulse_video.read_bytes(),pulse_video.stat().st_mtime_ns)
    pipeline = Pipeline(jobs)
    assert pipeline.prepare(job['job_id'])['state'] == 'ready'
    assert pipeline.prepare(job['job_id'])['state'] == 'ready'
    assert len(calls) == 1
    assert jobs.read(job['job_id'],'audio/activity.json') == []  # middle pulse prevents a 2.5s silence
    assert (pulse_video.read_bytes(),pulse_video.stat().st_mtime_ns) == before


def test_silero_vad_is_local_and_required_for_silence(tmp_path):
    from video_editor.activity import detect
    source=tmp_path/'silent.wav'
    with wave.open(str(source),'wb') as handle:
        handle.setparams((1,2,16000,0,'NONE','not compressed'))
        handle.writeframes(b'\x00\x00'*16000*4)
    result=detect(source,4)
    assert result == [dict(start=0,end=4,vad_clear=True,strict_clear=True,peak_db=-120,rms_db=-120)]


def test_quiet_tone_is_measured_as_sound_even_below_silencedetect_threshold(tmp_path):
    from video_editor.activity import detect
    source=tmp_path/'quiet-tone.wav'
    with wave.open(str(source),'wb') as handle:
        handle.setparams((1,2,16000,0,'NONE','not compressed'))
        handle.writeframes(b''.join(struct.pack('<h',int(160*math.sin(2*math.pi*440*i/16000))) for i in range(64000)))
    result=detect(source,4)
    assert result
    assert result[0]['peak_db']>-55 and result[0]['rms_db']>-65
    assert not result[0]['strict_clear']


def test_mono_phase_cancellation_does_not_authorize_native_silence(tmp_path):
    from video_editor.activity import detect
    mono=tmp_path/'mono.wav';stereo=tmp_path/'stereo.wav'
    with wave.open(str(mono),'wb') as handle:
        handle.setparams((1,2,16000,0,'NONE','not compressed'));handle.writeframes(b'\x00\x00'*64000)
    with wave.open(str(stereo),'wb') as handle:
        handle.setparams((2,2,16000,0,'NONE','not compressed'))
        handle.writeframes(b''.join(struct.pack('<hh',value,-value) for value in
                           (int(160*math.sin(2*math.pi*440*i/16000)) for i in range(64000))))
    result=detect(mono,4,stereo)
    assert result and result[0]['vad_clear']
    assert result[0]['peak_db']==-120
    assert not result[0]['strict_clear']


def test_encoder_sets_bounded_constant_quality():
    from video_editor.export.renderer import _encoder, max_bitrate
    hd60 = dict(width=1920, height=1080, fps=60)
    nvenc = _encoder('h264_nvenc', DEFAULT_CONFIG, hd60)
    assert nvenc[nvenc.index('-cq') + 1] == '21'
    assert nvenc[nvenc.index('-maxrate') + 1] == '20000000'
    assert '-pix_fmt' not in nvenc  # CUDA frames from hardware decode go straight in
    x264 = _encoder('libx264', DEFAULT_CONFIG, hd60)
    assert x264[x264.index('-crf') + 1] == '20' and x264[x264.index('-pix_fmt') + 1] == 'yuv420p'
    # The cap follows pixel rate, within bounds; missing legacy dimensions use the reference.
    assert max_bitrate(DEFAULT_CONFIG, dict(width=1920, height=1080, fps=30)) == 10_000_000
    assert max_bitrate(DEFAULT_CONFIG, dict(width=3840, height=2160, fps=60)) == 80_000_000
    assert max_bitrate(DEFAULT_CONFIG, dict(width=640, height=360, fps=30)) == 6_000_000
    assert max_bitrate(DEFAULT_CONFIG, dict(fps=30)) == 20_000_000


def test_punch_in_export_keeps_size_duration_and_sync(jobs, ready, pulse_video):
    job = ready('pulses.mp4', duration=6, segments=[dict(start=p, end=p+.12, text=f'Pulso {i}',
               words=[dict(word=f'Pulso {i}', start=p, end=p+.12)]) for i, p in enumerate([.5, 2.5, 5])])
    jid = job['job_id']
    jobs.update(jid, media=dict(job['media'], width=160, height=90))
    from video_editor.editing import mutate
    mutate(jobs, jid, dict(expected_revision=0, action='cut', start=2.4, end=2.7))  # removes speech
    worker = Worker()
    exports = Exports(jobs, worker, config=dict(DEFAULT_CONFIG, video_codec='libx264',
                                                punch_in=dict(enabled=True, scale=1.2)))
    item = exports.request(jid, 1)
    worker.queue.join()
    worker.close()
    exported = exports.get(jid, item['render_id'])
    assert exported['state'] == 'complete' and exported['punch_in_changes'] == 1
    graph = jobs.path(jid, f"exports/{item['render_id']}/filtergraph.txt").read_text(encoding='utf-8')
    assert graph.count('crop=') == 1 and 'scale=160:90' in graph
    output = jobs.path(jid, f"exports/{item['render_id']}/video.mp4")
    info = media.probe(output)
    assert (info['width'], info['height']) == (160, 90)
    assert info['duration'] == pytest.approx(5.7, abs=1/30 * 2)
    flashes, sounds = _signals(output, output.parent)
    assert flashes == pytest.approx([.5, 4.7], abs=1/30) and sounds == pytest.approx(flashes, abs=1/30)
