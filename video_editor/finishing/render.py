"""Render finishing layers in pieces, then concatenate and reuse the base audio."""
import json
import subprocess
from fractions import Fraction
from pathlib import Path

from PIL import Image

from video_editor import media
from video_editor.encoding import encoder_args, exact_timescale, gpu_error, pick_encoder
from video_editor.export.renderer import DEFAULT_CONFIG
from video_editor.finishing import cards, layers, models

W, H = 1920, 1080


def _run(command):
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace')
    if result.returncode:
        raise RuntimeError(result.stderr[-4000:] or 'ffmpeg falhou.')


def _layer_files(plan, work):
    directory = work / 'layers'
    directory.mkdir(parents=True, exist_ok=True)
    files = {}
    for index, item in enumerate(plan.overlays):
        path = directory / f'overlay_{index:03d}.png'
        layers.save_layer(item, path)
        files[('overlay', index)] = path
    for index, item in enumerate(plan.banners):
        path = directory / f'banner_{index:03d}.png'
        cards.save_banner(item, path)
        files[('banner', index)] = path
    card_files = {}
    for index, item in enumerate(plan.cards):
        path = directory / f'card_{index:03d}.png'
        cards.save_card(item, path)
        card_files[item] = path
    return files, card_files


def _composite(paths, size):
    frame = Image.new('RGBA', size, (0, 0, 0, 0))
    for path in paths:
        image = Image.open(path).convert('RGBA')
        if image.size != size:
            image = image.resize(size, Image.LANCZOS)
        frame.alpha_composite(image)
    return frame


def _base_ranges(plan, duration):
    boundaries = {0.0, duration}
    for item in plan.layers():
        boundaries.add(max(0.0, min(duration, item['start'])))
        boundaries.add(max(0.0, min(duration, item['end'])))
    for card in plan.cards:
        boundaries.add(max(0.0, min(duration, card.at)))
    ordered = sorted(boundaries)
    result = []
    cards_left = sorted(plan.cards, key=lambda card: card.at)
    card_index = 0
    for index, left in enumerate(ordered[:-1]):
        right = ordered[index + 1]
        while card_index < len(cards_left) and abs(cards_left[card_index].at - left) < 1e-9:
            result.append(('card', left, left, cards_left[card_index]))
            card_index += 1
        if right <= left:
            continue
        active = [
            item for item in plan.layers()
            if item['start'] < right and item['end'] > left
        ]
        result.append(('base', left, right, active))
    while card_index < len(cards_left):
        card = cards_left[card_index]
        if card.at > duration + 1e-9:
            raise ValueError('Card depois do fim do vídeo.')
        result.append(('card', duration, duration, card))
        card_index += 1
    return result


def _audio_pieces(plan, duration):
    pieces = []
    left = 0.0
    for card in sorted(plan.cards, key=lambda item: item.at):
        if card.at > duration + 1e-9:
            raise ValueError('Card depois do fim do vídeo.')
        if card.at > left:
            pieces.append(('base', left, card.at, None))
        pieces.append(('card', card.at, card.at, card))
        left = card.at
    if left < duration:
        pieces.append(('base', left, duration, None))
    return pieces


def _audio_filter(base_info, pieces):
    sample_rate = int(base_info.get('audio_sample_rate') or 48000)
    channels = int(base_info.get('audio_channels') or 2)
    layout = 'stereo' if channels == 2 else 'mono'
    filters, labels = [], []
    for index, (kind, left, right, value) in enumerate(pieces):
        if kind == 'base':
            filters.append(
                f'[0:a]atrim=start={left:.6f}:end={right:.6f},asetpts=PTS-STARTPTS[a{index}]'
            )
            labels.append(f'[a{index}]')
        else:
            duration = value.duration
            filters.append(
                f'anullsrc=channel_layout={layout}:sample_rate={sample_rate},'
                f'atrim=duration={duration:.6f},asetpts=PTS-STARTPTS[s{index}]'
            )
            labels.append(f'[s{index}]')
    filters.append(''.join(labels) + f'concat=n={len(labels)}:v=0:a=1[aout]')
    return ';'.join(filters)


def _probe_audio(path):
    try:
        output = media.run([
            'ffprobe', '-v', 'error', '-select_streams', 'a:0',
            '-show_entries', 'stream=sample_rate,channels', '-of', 'json', str(path),
        ])
        stream = json.loads(output)['streams'][0]
        return dict(audio_sample_rate=int(stream.get('sample_rate', 48000)),
                    audio_channels=int(stream.get('channels', 2)))
    except (OSError, ValueError, KeyError, IndexError, RuntimeError):
        return {}


def _probe_video(path):
    output = media.run([
        'ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height,avg_frame_rate,duration:format=duration',
        '-of', 'json', str(path),
    ])
    data = json.loads(output)
    stream = data['streams'][0]
    duration = stream.get('duration') or data['format']['duration']
    return dict(
        width=int(stream['width']), height=int(stream['height']),
        fps=float(Fraction(stream.get('avg_frame_rate', '30/1'))),
        duration=float(duration),
    )


MIN_COPY_SECONDS = 2.0


def _keyframes(path):
    """Keyframe times of an H.264 yuv420p video; [] when copying is not safe."""
    try:
        stream = json.loads(media.run([
            'ffprobe', '-v', 'error', '-select_streams', 'v:0',
            '-show_entries', 'stream=codec_name,pix_fmt,time_base,avg_frame_rate',
            '-of', 'json', str(path),
        ]))['streams'][0]
        if stream.get('codec_name') != 'h264' or stream.get('pix_fmt') != 'yuv420p':
            return []
        # Copied packets keep the base's timestamps. Unless one frame is a whole number of
        # time-base ticks (e.g. 1/15360 at 60 fps), they jitter by a tick and the joins come
        # out with non-monotonic timestamps; then every stretch is re-encoded instead.
        time_base = Fraction(stream.get('time_base') or '0/1')
        rate = Fraction(stream.get('avg_frame_rate') or '0/1')
        if not time_base or not rate or (1 / (rate * time_base)).denominator != 1:
            return []
        output = media.run([
            'ffprobe', '-v', 'error', '-select_streams', 'v:0', '-skip_frame', 'nokey',
            '-show_entries', 'frame=pts_time', '-of', 'csv=p=0', str(path),
        ])
    except (OSError, ValueError, KeyError, IndexError, RuntimeError):
        return []
    times = []
    for line in output.split():
        value = line.split(',')[0].strip()
        try:
            times.append(float(value))
        except ValueError:
            continue
    return sorted(times)


def _copy_spans(left, right, keyframes, fps):
    """Split a stretch without layers into (start, end, copy) parts: re-encode from left
    to the first keyframe, copy between keyframes, re-encode the tail. [] = encode all."""
    inside = [time for time in keyframes if left - 0.5 / fps <= time <= right + 0.5 / fps]
    if len(inside) < 2 or inside[-1] - inside[0] < MIN_COPY_SECONDS:
        return []
    first, last = inside[0], inside[-1]
    spans = []
    if first - left > 0.5 / fps:
        spans.append((left, first, False))
    spans.append((max(left, first), last, True))
    if right - last > 0.5 / fps:
        spans.append((last, right, False))
    return spans


def _encode_range(base, start, frames, fps, out, encode):
    _run([
        media.binary('ffmpeg'), '-nostdin', '-y', '-v', 'error',
        '-ss', f'{start:.6f}', '-i', str(base),
        '-frames:v', str(frames), '-an', *encode,
        '-pix_fmt', 'yuv420p', '-r', str(fps), str(out),
    ])


def _card_fade(duration):
    fade = min(.2, max(0.0, duration / 4))
    if fade <= 0:
        return 'null'
    return (
        f'fade=t=in:st=0:d={fade:.6f},'
        f'fade=t=out:st={max(0.0, duration - fade):.6f}:d={fade:.6f}'
    )


def render(base, plan, output, work_dir=None, encoder=None, master_audio=None, progress=None,
           sound_seed=None, copy_unchanged=True):
    base = Path(base)
    output = Path(output)
    if isinstance(plan, (str, Path)):
        plan = models.load_plan(plan)
    if master_audio is None:
        master_audio = getattr(plan, 'audio', None) is not None
    info = _probe_video(base)
    fps = float(info['fps'])
    duration = float(info['duration'])
    size = (int(info['width']), int(info['height']))
    work = Path(work_dir) if work_dir else output.parent / (output.stem + '-parts')
    work.mkdir(parents=True, exist_ok=True)
    files, card_files = _layer_files(plan, work)
    pieces = _base_ranges(plan, duration)
    total_parts = len(pieces)
    if progress:
        progress(0, total_parts)
    encoder = pick_encoder(encoder)
    encode = encoder_args(encoder, DEFAULT_CONFIG, info)
    encoder_fallback = False
    total_frames = round(duration * fps)
    rendered = []
    copied_frames = 0
    keyframes = _keyframes(base) if copy_unchanged else []
    for index, (kind, left, right, value) in enumerate(pieces):
        if kind == 'base':
            frames = round(right * fps) - round(left * fps)
            if frames <= 0:
                continue
            layer_files = [
                files[('overlay', item['index'])]
                if item['kind'] == 'overlay'
                else files[('banner', item['index'])]
                for item in value
            ]
            part = work / f'part_{index:03d}.mp4'
            if layer_files:
                merged = work / f'part_{index:03d}.png'
                _composite(layer_files, size).save(merged)

                def build(args):
                    return [
                        media.binary('ffmpeg'), '-nostdin', '-y', '-v', 'error',
                        '-ss', f'{left:.6f}', '-i', str(base),
                        '-loop', '1', '-framerate', str(fps), '-i', str(merged),
                        '-filter_complex',
                        '[0:v][1:v]overlay=0:0:shortest=1,format=yuv420p[v]',
                        '-map', '[v]', '-frames:v', str(frames), '-an', *args,
                        '-pix_fmt', 'yuv420p', '-r', str(fps), str(part),
                    ]
            else:
                spans = _copy_spans(left, right, keyframes, fps)
                if spans:
                    for number, (start, end, copy) in enumerate(spans):
                        sub = work / f'part_{index:03d}_{number}.mp4'
                        count = round(end * fps) - round(start * fps)
                        if count <= 0:
                            continue
                        if copy:
                            # Between two keyframes of the export the frames are copied
                            # as they are: no second encode, no quality loss, no time.
                            # Seeking half a frame past the keyframe lands exactly on it.
                            _run([
                                media.binary('ffmpeg'), '-nostdin', '-y', '-v', 'error',
                                '-ss', f'{start + 0.5 / fps:.6f}', '-i', str(base),
                                '-frames:v', str(count), '-an', '-c:v', 'copy',
                                '-avoid_negative_ts', 'make_zero', str(sub),
                            ])
                        else:
                            try:
                                _encode_range(base, start, count, fps, sub, encode)
                            except RuntimeError as error:
                                if encoder != 'h264_nvenc' or not gpu_error(error):
                                    raise
                                encoder = 'libx264'
                                encode = encoder_args(encoder, DEFAULT_CONFIG, info)
                                encoder_fallback = True
                                _encode_range(base, start, count, fps, sub, encode)
                        rendered.append((sub, count, 'base', start, end))
                        copied_frames += count if copy else 0
                    if progress:
                        progress(index + 1, total_parts)
                    continue

                def build(args):
                    return [
                        media.binary('ffmpeg'), '-nostdin', '-y', '-v', 'error',
                        '-ss', f'{left:.6f}', '-i', str(base),
                        '-frames:v', str(frames), '-an', *args,
                        '-pix_fmt', 'yuv420p', '-r', str(fps), str(part),
                    ]

            def execute(builder):
                nonlocal encoder, encode, encoder_fallback
                try:
                    _run(builder(encode))
                except RuntimeError as error:
                    if encoder != 'h264_nvenc' or not gpu_error(error):
                        raise
                    encoder = 'libx264'
                    encode = encoder_args(encoder, DEFAULT_CONFIG, info)
                    encoder_fallback = True
                    _run(builder(encode))

            execute(build)
            rendered.append((part, frames, 'base', left, right))
        else:
            card = value
            frames = round(card.duration * fps)
            if frames <= 0:
                continue
            part = work / f'part_{index:03d}.mp4'
            image = card_files[card]
            if size != (W, H):
                resized = work / f'card_{index:03d}_{size[0]}x{size[1]}.png'
                Image.open(image).convert('RGBA').resize(size, Image.LANCZOS).save(resized)
                image = resized
            def build(args):
                return [
                    media.binary('ffmpeg'), '-nostdin', '-y', '-v', 'error',
                    '-loop', '1', '-framerate', str(fps), '-i', str(image),
                    '-vf', _card_fade(card.duration),
                    '-frames:v', str(frames), '-an', *args,
                    '-pix_fmt', 'yuv420p', '-r', str(fps), str(part),
                ]

            def execute(builder):
                nonlocal encoder, encode, encoder_fallback
                try:
                    _run(builder(encode))
                except RuntimeError as error:
                    if encoder != 'h264_nvenc' or not gpu_error(error):
                        raise
                    encoder = 'libx264'
                    encode = encoder_args(encoder, DEFAULT_CONFIG, info)
                    encoder_fallback = True
                    _run(builder(encode))

            execute(build)
            rendered.append((part, frames, 'card', card.at, card.at))
        if progress:
            progress(index + 1, total_parts)
    total_frames = sum(frames for _, frames, *_ in rendered)
    list_path = work / 'concat.txt'
    # Exact durations (frames / fps) instead of what each container reports: with B-frames
    # a re-encoded part can report one frame short, and its last frame then collides with
    # the next part's first one (non-monotonic timestamps at the join).
    list_path.write_text(
        'ffconcat version 1.0\n' + ''.join(
            f"file '{part.resolve().as_posix()}'\nduration {frames / fps:.9f}\n"
            for part, frames, *_ in rendered
        ),
        encoding='utf-8',
    )
    joined = work / 'joined.mp4'
    _run([
        media.binary('ffmpeg'), '-nostdin', '-y', '-v', 'error',
        '-f', 'concat', '-safe', '0', '-i', str(list_path),
        '-c', 'copy', '-movflags', '+faststart',
        '-video_track_timescale', str(exact_timescale(fps)), str(joined),
    ])
    temporary = output.with_suffix('.tmp.mp4')
    sound_result = None
    base_info = _probe_audio(base) if plan.cards else {}
    if plan.cards and base_info:
        filter_text = _audio_filter(base_info, _audio_pieces(plan, duration))
        _run([
            media.binary('ffmpeg'), '-nostdin', '-y', '-v', 'error',
            '-i', str(base), '-i', str(joined),
            '-filter_complex', filter_text,
            '-map', '1:v', '-map', '[aout]', '-c:v', 'copy',
            '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart',
            '-t', f'{total_frames / fps:.6f}', str(temporary),
        ])
    elif plan.cards:
        _run([
            media.binary('ffmpeg'), '-nostdin', '-y', '-v', 'error',
            '-i', str(joined), '-map', '0:v', '-c', 'copy',
            '-movflags', '+faststart', str(temporary),
        ])
    else:
        _run([
            media.binary('ffmpeg'), '-nostdin', '-y', '-v', 'error',
            '-i', str(joined), '-i', str(base),
            '-map', '0:v', '-map', '1:a?', '-c', 'copy',
            '-movflags', '+faststart', str(temporary),
        ])
    if getattr(plan, 'sound', None) and _probe_audio(temporary):
        from video_editor.finishing import sound
        voiced = output.with_suffix('.sound.mp4')
        try:
            added = sound.mix(temporary, voiced, plan, total_frames / fps, work,
                              seed=sound_seed or output.parent.name)
        except RuntimeError:
            # Sound design is a bonus: a failed mix must not cost the user the video.
            added = None
            voiced.unlink(missing_ok=True)
        if added:
            voiced.replace(temporary)
            sound_result = added
    mastered = None
    if master_audio and _probe_audio(temporary):
        from video_editor.finishing import audio
        mastered = audio.master(
            temporary, output, options=getattr(plan, 'audio', None) or {}
        )
        temporary.unlink(missing_ok=True)
    else:
        temporary.replace(output)
    return dict(path=str(output), duration=round(total_frames / fps, 3),
                frames=total_frames, fps=fps, encoder=encoder, work=str(work),
                audio=mastered, encoder_fallback=encoder_fallback, sound=sound_result,
                copied_frames=copied_frames)
