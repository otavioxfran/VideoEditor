"""Finishing plans: cards, banners and image overlays expressed as job data."""
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

MODES = {
    'cover', 'contain', 'cutout', 'badge', 'badge_big',
    'fill', 'panel_white', 'panel_dark', 'dark',
}
LABELS = {'CORREÇÃO', 'ATENÇÃO'}
IMAGE_MODES = MODES - {'dark', 'panel_white', 'panel_dark'}
CARD_DURATION = 4.0
SOUNDS = {'whoosh', 'boom', 'pop', 'none'}


@dataclass(frozen=True)
class Overlay:
    start: float
    end: float
    image: Path | None
    mode: str
    webcam_rect: tuple[float, float, float, float] | None = None
    # Effect when the layer appears: whoosh, boom, pop, none, or None for the mode default.
    sound: str | None = None


@dataclass(frozen=True)
class Card:
    at: float
    label: str
    lines: tuple[str, ...]
    note: str = ''
    duration: float = CARD_DURATION


@dataclass(frozen=True)
class Banner:
    start: float
    end: float
    first: str
    second: str


@dataclass
class Plan:
    overlays: list[Overlay] = field(default_factory=list)
    cards: list[Card] = field(default_factory=list)
    banners: list[Banner] = field(default_factory=list)
    source: Path | None = None
    audio: dict | None = None
    sound: dict | None = None

    def layers(self):
        items = [
            dict(kind='overlay', index=index, start=item.start, end=item.end,
                 image=item.image, mode=item.mode, webcam_rect=item.webcam_rect)
            for index, item in enumerate(self.overlays)
        ]
        items.extend(
            dict(kind='banner', index=index, start=item.start, end=item.end,
                 image=None, mode='banner', webcam_rect=None, banner=item)
            for index, item in enumerate(self.banners)
        )
        return sorted(items, key=lambda item: (item['start'], item['end']))


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{name} precisa ser numérico.')
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f'{name} precisa ser finito.')
    return value


def _rect(value):
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError('webcam_rect precisa ser [x, y, w, h].')
    values = tuple(_number(item, 'webcam_rect') for item in value)
    if values[2] <= 0 or values[3] <= 0 or values[0] < 0 or values[1] < 0:
        raise ValueError('webcam_rect inválido.')
    return values


def _path(value, base):
    if value is None:
        return None
    path = Path(str(value))
    if not path.is_absolute():
        path = base / path
    return path


def load_plan(path):
    source = Path(path).resolve()
    data = json.loads(source.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('O plano precisa ser um objeto JSON.')
    base = source.parent
    overlays = []
    for index, item in enumerate(data.get('overlays') or []):
        mode = str(item.get('mode', ''))
        if mode not in MODES:
            raise ValueError(f'Modo de overlay inválido: {mode!r}.')
        image = _path(item.get('image'), base)
        if mode in IMAGE_MODES and image is None:
            raise ValueError(f'Overlay {index}: imagem obrigatória no modo {mode}.')
        start = _number(item.get('start'), 'overlay.start')
        end = _number(item.get('end'), 'overlay.end')
        if start < 0 or end <= start:
            raise ValueError(f'Overlay {index}: intervalo inválido.')
        sound = item.get('sound')
        if sound is not None and sound not in SOUNDS:
            raise ValueError(f'Overlay {index}: som inválido {sound!r}.')
        overlays.append(Overlay(start, end, image, mode, _rect(item.get('webcam_rect')), sound))
    cards = []
    for index, item in enumerate(data.get('cards') or []):
        label = str(item.get('label', '')).upper()
        if label not in LABELS:
            raise ValueError(f'Card {index}: label precisa ser CORREÇÃO ou ATENÇÃO.')
        lines = tuple(str(value) for value in item.get('lines') or [])
        if not lines:
            raise ValueError(f'Card {index}: lines não pode ser vazio.')
        at = _number(item.get('at'), 'card.at')
        duration = _number(item.get('duration', CARD_DURATION), 'card.duration')
        if at < 0 or duration <= 0:
            raise ValueError(f'Card {index}: tempo/duração inválidos.')
        cards.append(Card(at, label, lines, str(item.get('note', '')), duration))
    banners = []
    for index, item in enumerate(data.get('banners') or []):
        start = _number(item.get('start'), 'banner.start')
        end = _number(item.get('end'), 'banner.end')
        if start < 0 or end <= start:
            raise ValueError(f'Banner {index}: intervalo inválido.')
        banners.append(Banner(start, end, str(item.get('first', '')), str(item.get('second', ''))))
    overlays.sort(key=lambda item: (item.start, item.end))
    banners.sort(key=lambda item: (item.start, item.end))
    cards.sort(key=lambda item: item.at)
    for previous, current in zip(overlays, overlays[1:]):
        if current.start < previous.end:
            raise ValueError('Overlays sobrepostos.')
    for previous, current in zip(banners, banners[1:]):
        if current.start < previous.end:
            raise ValueError('Banners sobrepostos.')
    audio = data.get('audio')
    if audio is not None and not isinstance(audio, dict):
        raise ValueError('audio precisa ser um objeto JSON.')
    sound = data.get('sound')
    if sound is not None and not isinstance(sound, dict):
        raise ValueError('sound precisa ser um objeto JSON.')
    return Plan(overlays, cards, banners, source, audio, sound)


def map_time(seconds, cards):
    """Base time -> final time; content at a card point plays after that card."""
    seconds = float(seconds)
    return seconds + sum(card.duration for card in cards if card.at <= seconds)


def map_end(seconds, cards):
    """Base end time -> final end time; an interval ending at a card stays before it."""
    seconds = float(seconds)
    return seconds + sum(card.duration for card in cards if card.at < seconds)


def card_start(card, cards):
    return card.at + sum(item.duration for item in cards if item.at < card.at)


def map_range(start, end, cards):
    if end < start:
        raise ValueError('Intervalo inválido.')
    return map_time(start, cards), map_end(end, cards)


def final_duration(duration, cards):
    return float(duration) + sum(card.duration for card in cards)


TIMESTAMP = re.compile(r'(\d{2}):(\d{2}):(\d{2}),(\d{3})')


def _clock(seconds):
    milliseconds = int(round(seconds * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}'


def _seconds(clock):
    hours, minutes, rest = clock.split(':')
    seconds, milliseconds = rest.split(',')
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(milliseconds) / 1000


def shift_srt(text, cards):
    if not cards:
        return text
    blocks = re.split(r'(\n\s*\n)', text.strip())
    output = []
    for block in blocks:
        if '-->' not in block:
            output.append(block)
            continue
        lines = block.split('\n')
        times = lines[1].split(' --> ')
        lines[1] = f'{_clock(map_time(_seconds(times[0]), cards))} --> {_clock(map_end(_seconds(times[1]), cards))}'
        output.append('\n'.join(lines))
    return ''.join(output).strip() + '\n'


def apply_srt(source, target, cards):
    text = Path(source).read_text(encoding='utf-8')
    Path(target).write_text(shift_srt(text, cards), encoding='utf-8')
    return Path(target)
