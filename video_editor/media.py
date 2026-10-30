import json
import subprocess
import shutil
from pathlib import Path
from fractions import Fraction
from video_editor.safety import destination
import anyio
from starlette.responses import FileResponse


class LocalFileResponse(FileResponse):
    """Stop disk reads when a player abandons a range request."""

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await super().__call__(scope, receive, send)
        async with anyio.create_task_group() as tasks:
            async def disconnected():
                while True:
                    if (await receive())['type'] == 'http.disconnect':
                        tasks.cancel_scope.cancel()
                        return

            async def forward(message):
                await send(message)
                # Header middleware needs a response even if the client already left.
                if message['type'] == 'http.response.start':
                    tasks.start_soon(disconnected)

            try:
                await super().__call__(scope, receive, forward)
            finally:
                tasks.cancel_scope.cancel()


def binary(name):
    path = shutil.which(name)
    if not path:
        raise FileNotFoundError(f'{name} não encontrado no PATH.')
    return path


def run(command):
    command = [binary(command[0]), *command[1:]]
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace')
    if result.returncode:
        raise RuntimeError(result.stderr[-4000:] or f'Processo de mídia interrompido (código {result.returncode}).')
    return result.stdout


def probe(path):
    result = json.loads(run(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)]))
    video = next((s for s in result['streams'] if s['codec_type'] == 'video'), None)
    audio = next((s for s in result['streams'] if s['codec_type'] == 'audio'), None)
    if not video or not audio:
        raise ValueError("É necessário vídeo com faixa de áudio.")
    fps = float(Fraction(video.get('avg_frame_rate', '30/1')))
    origin = float(video.get('start_time', 0))
    format_start=float(result['format'].get('start_time',0))
    # Normalize the job clock to the first video frame, preserving audio's offset.
    video_duration = float(video.get('duration') or (float(result['format']['duration']) +
                     float(result['format'].get('start_time', 0)) - origin))
    audio_start=float(audio.get('start_time',0))
    audio_end=(audio_start+float(audio['duration'])-origin if audio.get('duration') else
               float(result['format']['duration'])+format_start-origin)
    duration=max(video_duration,audio_end)
    if duration <= 0 or fps <= 0:
        raise ValueError("Duração ou frame rate inválidos.")
    return dict(duration=duration, width=video['width'], height=video['height'], fps=fps,
                video_codec=video['codec_name'], audio_codec=audio['codec_name'],
                video_start=origin, audio_start=float(audio.get('start_time', 0)),
                origin=origin-format_start, source_video_origin=origin, container_start=format_start, timing_contract=2,
                video_duration=video_duration,tail_padding=max(0,duration-video_duration),
                variable_frame_rate=video.get('r_frame_rate') != video.get('avg_frame_rate'),
                container=result['format'].get('format_name', ''))


def check_supported(info):
    if info['video_codec'] != 'h264' or info['audio_codec'] != 'aac':
        raise ValueError("Preview direto requer MP4 H.264/AAC.")
    # Nonzero stream origins require explicit normalization, rather than a silent sync change.
    if max(abs(info['video_start']), abs(info['audio_start'])) > .001:
        raise ValueError("Mídia com offsets de streams exige normalização antes de criar o job.")


def extract(source, output, root, protected_sources=(), origin=0):
    sources = [source, *protected_sources]
    output = destination(output, root, sources)
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = destination(output.with_suffix('.partial.wav'), root, sources)
    seek=['-ss',f'{origin:.9f}'] if origin>0 else []
    run(['ffmpeg', '-nostdin', '-y', '-v', 'error', *seek, '-i', str(source), '-map','0:a:0','-vn',
         '-af', 'aresample=async=1:first_pts=0', '-ac', '1',
         '-ar', '16000', '-c:a', 'pcm_s16le', str(partial)])
    partial.replace(output)
    return output


def needs_proxy(info, source):
    return (str(source).lower().endswith(('.mov', '.mkv')) or info['video_codec'] != 'h264' or
            info['audio_codec'] != 'aac' or info.get('variable_frame_rate') or
            max(abs(info['video_start']), abs(info['audio_start'])) > .001)


def preview_proxy(source, output, root, info, protected_sources=()):
    """Only preview is resized; export decodes the original using the same source clock."""
    output = destination(output, root, [source, *protected_sources])
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = destination(output.with_suffix('.partial.mp4'), root, [source, *protected_sources])
    origin, fps = info.get('origin', 0), info['fps']
    pad=info.get('tail_padding',0)
    padding=f',tpad=stop_mode=clone:stop_duration={pad:.9f}' if pad>.001 else ''
    graph = f"fps={fps:.9f}{padding},scale=w='min(1280,iw)':h=-2,format=yuv420p"
    seek=['-ss',f'{origin:.9f}'] if origin>0 else []
    audio=info.get('render_audio_path')
    inputs=[*seek,'-an','-i',str(source),'-i',audio] if audio else [*seek,'-i',str(source)]
    args = [*inputs, '-vf', graph, '-map', '0:v:0', '-map', '1:a:0' if audio else '0:a:0',
            '-t', str(info['duration']), '-c:a', 'aac', '-movflags', '+faststart', str(partial)]
    import subprocess
    result = subprocess.run([binary('ffmpeg'), '-nostdin', '-y', '-v', 'error', '-filter_complex_threads','2', *args[:-1],
                             '-c:v', 'h264_nvenc', '-preset', 'p4', args[-1]], capture_output=True)
    if result.returncode:
        if not any(term in result.stderr.lower() for term in (b'nvenc',b'cuda',b'nvidia',b'no capable devices')):
            raise RuntimeError(result.stderr.decode('utf-8',errors='replace')[-3000:])
        run(['ffmpeg', '-nostdin', '-y', '-v', 'error', '-filter_complex_threads','2', *args[:-1], '-c:v', 'libx264',
             '-threads:v','4','-preset', 'veryfast', args[-1]])
    check_supported(probe(partial))
    partial.replace(output)
    return output


def extract_render_audio(source,output,root,protected_sources=(),origin=0):
    """Decode once at source channel layout; this is also the acoustic/VAD parent."""
    output=destination(output,root,[source,*protected_sources])
    output.parent.mkdir(parents=True,exist_ok=True)
    partial=destination(output.with_suffix('.partial.wav'),root,[source,*protected_sources])
    seek=['-ss',f'{origin:.9f}'] if origin>0 else []
    run(['ffmpeg','-nostdin','-y','-v','error',*seek,'-i',str(source),'-map','0:a:0','-vn',
         '-af','aresample=async=1:first_pts=0','-ar','48000','-c:a','pcm_s24le',str(partial)])
    partial.replace(output)
    return output
