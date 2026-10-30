"""Punch-in framing: alternate a slight zoom at jump cuts, like an editor hiding them.

A static webcam shot makes every cut a visible head jump. Editors alternate the framing
(100% / ~110%) on the cuts that matter, so the cut reads as a choice. One plan, derived
from the timeline and transcript, drives both the browser preview and the export.
"""
from video_editor.channel_profile import load_profile

MIN_REMOVED = 1.5     # pause cuts shorter than this barely move the head: keep framing
MIN_HOLD = 2.0        # never change framing twice within this much output time
ANCHOR_Y = .4         # zoom around eye height rather than the frame centre
DEFAULT_SCALE = 1.1
MAX_SCALE = 1.25


def settings(profile=None):
    profile = load_profile() if profile is None else profile
    scale = float(profile.get('punch_in_scale', DEFAULT_SCALE))
    return dict(enabled=bool(profile.get('punch_in', False)), scale=min(MAX_SCALE, max(1.0, scale)))


def plan(timeline, transcript, config):
    """Scale for each keep, in order (1.0 = original framing)."""
    keeps = timeline['keeps']
    if not config or not config.get('enabled') or config.get('scale', 1) <= 1 or len(keeps) < 2:
        return [1.0] * len(keeps)
    words = [w for s in transcript['segments'] for w in s['words']]
    scales, current, last_change = [1.0], 1.0, 0.0
    for left, right in zip(keeps, keeps[1:]):
        start, end = left['source_end'], right['source_start']
        removed_speech = any(min(end, w['end']) - max(start, w['start']) > .05 for w in words)
        visible = removed_speech or end - start >= MIN_REMOVED
        settled = (right['output_start'] - last_change >= MIN_HOLD and
                   right['source_end'] - right['source_start'] >= MIN_HOLD)
        if visible and settled:
            current = config['scale'] if current == 1.0 else 1.0
            last_change = right['output_start']
        scales.append(current)
    return scales


def video_filter(scale, width, height):
    """Crop around eye height and scale back to the source size; '' when unzoomed."""
    if scale <= 1.0 or not width or not height:
        return ''
    return (f'crop=w=trunc(iw/{scale:.6f}/2)*2:h=trunc(ih/{scale:.6f}/2)*2:'
            f'x=(iw-ow)/2:y=(ih-oh)*{ANCHOR_Y},scale={width}:{height}:flags=lanczos,setsar=1')
