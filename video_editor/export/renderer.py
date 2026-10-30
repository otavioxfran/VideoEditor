"""Execute supplied keeps verbatim, with bounded filter fan-out on long edits."""
import subprocess
from video_editor import framing, media, storage
from video_editor.safety import destination

DEFAULT_CONFIG = dict(video_codec='h264_nvenc', audio_codec='aac', audio_bitrate='192k',
                      fade_seconds=.015, crossfade_seconds=.06, hardware_decode=True, contract=5,
                      nvenc_cq=21, x264_crf=20, reference_max_bitrate=20_000_000)

# The reference cap applies to 1080p60; other formats scale by pixel rate.
REFERENCE_PIXEL_RATE = 1920 * 1080 * 60
MIN_MAX_BITRATE, MAX_MAX_BITRATE = 6_000_000, 80_000_000

MAX_BATCH_KEEPS = 24


MIN_CROSSFADE_HALF = .002


def crossfade_halves(keeps, crossfade_seconds):
    """Half-length of the crossfade at each join, borrowed from the removed span on both sides."""
    halves = []
    for left, right in zip(keeps, keeps[1:]):
        removed = right['source_start'] - left['source_end']
        half = min(crossfade_seconds / 2, removed / 2,
                   (left['source_end'] - left['source_start']) / 4,
                   (right['source_end'] - right['source_start']) / 4)
        halves.append(half if half >= MIN_CROSSFADE_HALF else 0)
    return halves


def _graph(keeps, fade_seconds, offset=0, origin=None, fps=None, audio_input=0,tail_padding=0,crossfade_seconds=0,framing_filters=None):
    # Joins crossfade room tone instead of dipping to silence. Each side borrows `half`
    # from the removed span, so every join adds 2*half and the crossfade removes it again:
    # audio length stays exactly the sum of the keeps and A/V sync is unchanged.
    halves = crossfade_halves(keeps, crossfade_seconds)
    parts, videos = [], []
    for i, keep in enumerate(keeps):
        start, end = keep['source_start'] - offset, keep['source_end'] - offset
        video_clock = audio_clock = ''
        if origin is not None:
            video_clock = f'fps={fps:.9f},'
            audio_clock = 'aresample=async=1:first_pts=0,'
        if tail_padding>.001:
            video_clock+=f'tpad=stop_mode=clone:stop_duration={tail_padding:.9f},'
        before = halves[i - 1] if i else 0
        after = halves[i] if i < len(halves) else 0
        a_start, a_end = start - before, end + after
        duration = a_end - a_start
        fade = min(fade_seconds, (end - start) / 2)
        # Short fades remain only where nothing crossfades: batch edges and tiny removals.
        fades = (f',afade=t=in:d={fade:.9f}' if not before else '') + \
                (f',afade=t=out:st={duration-fade:.9f}:d={fade:.9f}' if not after else '')
        frame = f',{framing_filters[i]}' if framing_filters and framing_filters[i] else ''
        parts.append(f'[0:v:0]{video_clock}trim=start={start:.9f}:end={end:.9f},setpts=PTS-STARTPTS{frame}[v{i}]')
        parts.append(f'[{audio_input}:a:0]{audio_clock}atrim=start={a_start:.9f}:end={a_end:.9f},'
                     f'asetpts=PTS-STARTPTS{fades}[a{i}]')
        videos.append(f'[v{i}]')
    parts.append(''.join(videos) + f'concat=n={len(keeps)}:v=1:a=0[outv]')
    current = '[a0]'
    for i, half in enumerate(halves, start=1):
        label = '[outa]' if i == len(keeps) - 1 else f'[x{i}]'
        if half:
            parts.append(f'{current}[a{i}]acrossfade=d={2 * half:.9f}:c1=qsin:c2=qsin{label}')
        else:
            parts.append(f'{current}[a{i}]concat=n=2:v=0:a=1{label}')
        current = label
    if len(keeps) == 1:
        parts.append('[a0]anull[outa]')
    return ';\n'.join(parts)


def _execute(arguments):
    return subprocess.run([media.binary('ffmpeg'), '-nostdin', '-y', '-v', 'error',
                           '-filter_complex_threads', '2', *arguments],
                          capture_output=True, text=True, encoding='utf-8', errors='replace')


def max_bitrate(config, media_info):
    # Migrated legacy media may lack dimensions; fall back to the reference format.
    try:
        pixel_rate = float(media_info['width']) * float(media_info['height']) * float(media_info['fps'])
    except (KeyError, TypeError, ValueError):
        pixel_rate = REFERENCE_PIXEL_RATE
    scaled = config.get('reference_max_bitrate', DEFAULT_CONFIG['reference_max_bitrate']) * pixel_rate / REFERENCE_PIXEL_RATE
    return int(min(MAX_MAX_BITRATE, max(MIN_MAX_BITRATE, scaled)))


def _encoder(codec, config, media_info):
    # Constant quality bounded by a cap: unset NVENC defaults to ~2 Mbps, far below the raw.
    cap = max_bitrate(config, media_info)
    rate = ['-maxrate', str(cap), '-bufsize', str(2 * cap)]
    if codec == 'h264_nvenc':
        # No -pix_fmt here: hardware-decoded CUDA frames go straight to the encoder.
        return ['-c:v', codec, '-preset', 'p5', '-tune', 'hq', '-rc', 'vbr',
                '-cq', str(config.get('nvenc_cq', DEFAULT_CONFIG['nvenc_cq'])),
                '-b:v', str(int(cap * .7)), *rate, '-spatial-aq', '1', '-profile:v', 'high']
    return ['-c:v', codec, '-threads:v', '4', '-preset', 'fast',
            '-crf', str(config.get('x264_crf', DEFAULT_CONFIG['x264_crf'])), *rate, '-pix_fmt', 'yuv420p']


def _gpu_error(result):
    return any(word in result.stderr.lower() for word in ('nvenc', 'cuda', 'nvidia', 'no capable devices',
        'hwaccel','hardware acceleration','decoder surfaces','impossible to convert'))


def render(snapshot, output, root):
    source = snapshot['source']['path']
    sources = [source, *snapshot.get('protected_sources', [])]
    output = destination(output, root, sources)
    keeps = snapshot['timeline']['keeps']
    if not keeps:
        raise ValueError("Todos os intervalos foram removidos.")
    # One framing plan for the whole edit: batches must not restart the alternation.
    scales = framing.plan(snapshot['timeline'], snapshot.get('transcript') or dict(segments=[]),
                         snapshot['config'].get('punch_in'))
    any_zoom = any(scale > 1 for scale in scales)
    frames = [framing.video_filter(scale, snapshot['media'].get('width'), snapshot['media'].get('height'))
              or ('setsar=1' if any_zoom else '') for scale in scales]
    graph = destination(output.parent / 'filtergraph.txt', root, sources)
    partial = destination(output.parent / 'video.partial.mp4', root, sources)
    intermediates = []
    origin = snapshot['media'].get('origin') if snapshot['media'].get('timing_contract') == 2 else None
    clock_args = ['-ss',f'{origin:.9f}'] if origin is not None and origin>0 else []
    fps = snapshot['media']['fps']
    audio=snapshot.get('audio_source')
    audio_index=1 if audio else 0

    hardware_decode=False

    def inputs(seek=(),span=None):
        duration_args=['-t',f'{span:.9f}'] if span is not None else []
        decode_args=['-hwaccel','cuda','-hwaccel_output_format','cuda'] if hardware_decode else []
        args=[*decode_args,*seek,*duration_args,*(['-an'] if audio else []),'-i',source]
        if audio:
            # PCM is already on the normalized source clock; no codec priming is lost.
            offset=float(seek[1])-(origin or 0) if seek else 0
            args.extend([*(['-ss',f'{offset:.9f}'] if offset>0 else []),*duration_args,'-i',audio['path']])
        return args

    def execute(codec):
        if len(keeps) <= MAX_BATCH_KEEPS:
            storage.write_text(graph, _graph(keeps, snapshot['config']['fade_seconds'], origin=origin, fps=fps,audio_input=audio_index,tail_padding=snapshot['media'].get('tail_padding',0),crossfade_seconds=snapshot['config'].get('crossfade_seconds',0),framing_filters=frames))
            return _execute([*inputs(clock_args), '-/filter_complex', str(graph), '-map', '[outv]', '-map', '[outa]',
                             *_encoder(codec, snapshot['config'], snapshot['media']), '-c:a', 'aac', '-b:a', snapshot['config']['audio_bitrate'],
                             '-t',f"{snapshot['timeline']['duration']:.9f}",'-movflags', '+faststart', str(partial)])
        # Seeking limits decoding to each batch's source range. Video is encoded once;
        # PCM audio is concatenated before a single AAC encode, avoiding per-batch priming.
        manifest = destination(output.parent / 'concat.txt', root, sources)
        lines = ['ffconcat version 1.0']
        graphs = []
        for index, left in enumerate(range(0, len(keeps), MAX_BATCH_KEEPS)):
            batch = keeps[left:left + MAX_BATCH_KEEPS]
            # Decode AAC from before the kept boundary. Seeking exactly onto it can
            # discard a priming packet; STARTPTS would then hide lost leading audio.
            offset = max(0, batch[0]['source_start'] - 1)
            span = batch[-1]['source_end'] - offset
            batch_graph = destination(output.parent / f'filtergraph-{index:04d}.txt', root, sources)
            chunk = destination(output.parent / f'part-{index:04d}.mkv', root, sources)
            if chunk not in intermediates:
                intermediates.append(chunk)
            storage.write_text(batch_graph, _graph(batch, snapshot['config']['fade_seconds'], offset, origin, fps,audio_index,snapshot['media'].get('tail_padding',0),snapshot['config'].get('crossfade_seconds',0),frames[left:left + MAX_BATCH_KEEPS]))
            graphs.append(batch_graph.name)
            seek_time=offset+(origin or 0)
            seek = ['-ss', f'{seek_time:.9f}'] if seek_time else []
            batch_inputs=inputs(seek,span)
            result = _execute([*batch_inputs,
                               '-/filter_complex', str(batch_graph), '-map', '[outv]', '-map', '[outa]',
                               *_encoder(codec, snapshot['config'], snapshot['media']), '-c:a', 'pcm_s16le', str(chunk)])
            if result.returncode:
                return result
            duration = sum(k['source_end'] - k['source_start'] for k in batch)
            lines.extend([f"file '{chunk.name}'", f'duration {duration:.9f}'])
        storage.write_text(graph, '# Bounded graphs, executed in source order:\n' + '\n'.join(graphs))
        storage.write_text(manifest, '\n'.join(lines) + '\n')
        return _execute(['-f', 'concat', '-safe', '1', '-i', str(manifest), '-map', '0:v:0', '-map', '0:a:0',
                         '-c:v', 'copy', '-c:a', 'aac', '-b:a', snapshot['config']['audio_bitrate'],
                         '-movflags', '+faststart', str(partial)])

    codec = snapshot['config']['video_codec']
    # Punch-in crops in system memory; CUDA frames cannot be cropped, and CPU decode
    # measured as fast as NVDEC for these recordings.
    zoomed = any(scale > 1 for scale in scales)
    hardware_decode=(snapshot['config'].get('hardware_decode',False) and codec=='h264_nvenc' and not zoomed and
                     snapshot['media'].get('video_codec') in ('h264','hevc','vp9'))
    result = execute(codec)
    if result.returncode and hardware_decode and _gpu_error(result):
        hardware_decode=False
        result=execute(codec)
    if result.returncode and codec == 'h264_nvenc' and _gpu_error(result):
        codec = 'libx264'
        result = execute(codec)
    if result.returncode:
        raise RuntimeError(result.stderr[-4000:] or f'FFmpeg interrompido (código {result.returncode}).')
    info = media.probe(partial)
    tolerance = max(.1, 2 / snapshot['media']['fps'])
    if abs(info['duration'] - snapshot['timeline']['duration']) > tolerance:
        raise RuntimeError("Duração exportada diverge da timeline. Arquivo parcial não publicado.")
    partial.replace(output)
    for path in intermediates:
        destination(path, root, sources).unlink(missing_ok=True)
    return dict(actual_codec=codec,hardware_decode=hardware_decode,punch_in_changes=sum(a != b for a, b in zip(scales, scales[1:])), media=info, render_batches=max(1, (len(keeps) + MAX_BATCH_KEEPS - 1) // MAX_BATCH_KEEPS))
