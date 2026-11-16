"""Black correction cards and top banners, rendered with a clean system font."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1920, 1080
BOLD_CANDIDATES = (
    'C:/Windows/Fonts/segoeuib.ttf',
    'C:/Windows/Fonts/arialbd.ttf',
)
REGULAR_CANDIDATES = (
    'C:/Windows/Fonts/segoeui.ttf',
    'C:/Windows/Fonts/arial.ttf',
)


def _font(bold, size):
    candidates = BOLD_CANDIDATES if bold else REGULAR_CANDIDATES
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        from matplotlib import font_manager
        name = 'DejaVu Sans:bold' if bold else 'DejaVu Sans'
        return ImageFont.truetype(font_manager.findfont(name), size)
    except (ImportError, OSError, ValueError):
        return ImageFont.load_default(size)


def _fit(draw, text, bold, size, width, minimum=30):
    while size > minimum:
        font = _font(bold, size)
        if draw.textlength(text, font=font) <= width:
            return font
        size -= 2
    return _font(bold, minimum)


def build_card(card):
    image = Image.new('RGB', (W, H), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    tag_font = _font(True, 44)
    color = (255, 64, 96) if card.label == 'CORREÇÃO' else (255, 184, 0)
    fonts = [_fit(draw, line, True, 78, 1600) for line in card.lines]
    size = min(font.size for font in fonts)
    main = _font(True, size)
    small = _fit(draw, card.note, False, 46, 1600) if card.note else None
    heights = [70] + [round(size * 1.25)] * len(card.lines) + ([40, 60] if small else [])
    y = (H - sum(heights)) // 2
    tag_width = draw.textlength(card.label, font=tag_font)
    draw.rounded_rectangle(
        ((W - tag_width) / 2 - 22, y - 8, (W + tag_width) / 2 + 22, y + 58),
        radius=12, fill=color,
    )
    draw.text((W / 2, y + 25), card.label, font=tag_font, fill=(0, 0, 0), anchor='mm')
    y += 100
    for line in card.lines:
        draw.text((W / 2, y + size / 2), line, font=main, fill=(255, 255, 255), anchor='mm')
        y += round(size * 1.25)
    if small is not None:
        y += 40
        draw.text((W / 2, y + 23), card.note, font=small, fill=(170, 170, 170), anchor='mm')
    return image


BANNER_LEFT, BANNER_TOP, BANNER_MAX_RIGHT = 40, 36, 1200


def build_banner(banner):
    image = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    bold = _fit(draw, banner.first, True, 40, BANNER_MAX_RIGHT - BANNER_LEFT - 80)
    regular = _fit(draw, banner.second, False, 36, BANNER_MAX_RIGHT - BANNER_LEFT - 80)
    tag = _font(True, 28)
    width = max(draw.textlength(banner.first, font=bold), draw.textlength(banner.second, font=regular))
    right = min(BANNER_MAX_RIGHT, BANNER_LEFT + 40 + width + 40)
    draw.rounded_rectangle(
        (BANNER_LEFT, BANNER_TOP, right, BANNER_TOP + 190),
        radius=18, fill=(10, 10, 14, 225),
    )
    draw.rectangle(
        (BANNER_LEFT, BANNER_TOP + 18, BANNER_LEFT + 8, BANNER_TOP + 172),
        fill=(255, 64, 96, 255),
    )
    tag_width = draw.textlength('CORREÇÃO', font=tag)
    draw.rounded_rectangle(
        (BANNER_LEFT + 40, BANNER_TOP + 20, BANNER_LEFT + 40 + tag_width + 24, BANNER_TOP + 60),
        radius=8, fill=(255, 64, 96, 255),
    )
    draw.text((BANNER_LEFT + 52, BANNER_TOP + 40), 'CORREÇÃO', font=tag, fill=(0, 0, 0), anchor='lm')
    draw.text((BANNER_LEFT + 40, BANNER_TOP + 95), banner.first, font=bold, fill=(255, 255, 255), anchor='lm')
    draw.text((BANNER_LEFT + 40, BANNER_TOP + 145), banner.second, font=regular, fill=(200, 200, 200), anchor='lm')
    return image


def save_card(card, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    build_card(card).save(path)
    return path


def save_banner(banner, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    build_banner(banner).save(path)
    return path
