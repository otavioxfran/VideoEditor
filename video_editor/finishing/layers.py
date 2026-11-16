"""PNG finishing layers (1920x1080 RGBA) with the live-webcam hole kept transparent."""
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from video_editor.finishing import cutout as cutout_module

W, H = 1920, 1080
MAX_UPSCALE = 1.6
COVER_MARGIN = 70
CONTAIN_MARGIN = 150
BADGE = (560, 380)
BADGE_BIG = (640, 700)


def _value(item, key, default=None):
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _open(path):
    image = Image.open(path)
    image.seek(0)
    return image.convert('RGBA')


def fit_scale(width, height, box_width, box_height):
    if width <= 0 or height <= 0 or box_width <= 0 or box_height <= 0:
        return 0.0
    return min(box_width / width, box_height / height, MAX_UPSCALE)


def _resized(image, scale):
    return image.resize(
        (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
        Image.LANCZOS,
    )


def _punch_webcam(image, rect):
    if not rect:
        return image
    x, y, width, height = [int(round(value)) for value in rect]
    x, y = max(0, x), max(0, y)
    right, bottom = min(W, x + max(0, width)), min(H, y + max(0, height))
    if right <= x or bottom <= y:
        return image
    alpha = np.asarray(image.getchannel('A')).copy()
    alpha[y:bottom, x:right] = 0
    result = image.copy()
    result.putalpha(Image.fromarray(alpha))
    return result


def _cut_background(image):
    image = image.convert('RGBA')
    data = np.asarray(image)
    if (data[..., 3] < 250).mean() > .05:
        return image
    rgb = data[..., :3].astype(np.int16)
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    colors, counts = np.unique((border // 16) * 16 + 8, axis=0, return_counts=True)
    palette = colors[np.argsort(-counts)[:3]]
    near = np.zeros(rgb.shape[:2], np.uint8)
    for color in palette:
        near |= (np.abs(rgb - color).max(axis=2) < 36).astype(np.uint8)
    _, labels = cv2.connectedComponents(near, connectivity=8)
    edge = np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
    background = np.isin(labels, edge[edge > 0])
    keep = (~background).astype(np.uint8) * 255
    keep = cv2.morphologyEx(keep, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    out = data.copy()
    out[..., 3] = keep
    result = Image.fromarray(out)
    result.putalpha(result.getchannel('A').filter(ImageFilter.GaussianBlur(1)))
    return result


def _cutout(image, cam_x, source=None):
    image = image.convert('RGBA')
    if source is not None:
        alpha = np.asarray(image.getchannel('A'))
        if (alpha < 250).mean() < .05:
            image = _open(cutout_module.cutout(source))
    box = image.getchannel('A').point(lambda value: 255 if value > 20 else 0).getbbox()
    if box:
        image = image.crop(box)
    bottom = np.asarray(image.getchannel('A').crop((0, max(0, image.height - 2), image.width, image.height)))
    standing = (bottom > 128).mean() > .3
    box_w, box_h = (cam_x - 300, H - 140) if standing else (cam_x - 400, H - 420)
    scale = fit_scale(image.width, image.height, box_w, box_h)
    image = _resized(image, scale)
    result = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    y = H - image.height if standing else (H - image.height) // 2
    result.alpha_composite(image, ((cam_x - image.width) // 2, y))
    return result


def _badge(image, cam_x, target_w, target_h):
    logo = _cut_background(image)
    data = np.asarray(logo).copy()
    rgb = data[..., :3].astype(np.int16)
    light = (rgb.min(axis=2) > 185) & ((rgb.max(axis=2) - rgb.min(axis=2)) < 14)
    data[..., 3] = np.where(light, 0, data[..., 3])
    logo = Image.fromarray(data)
    box = logo.getchannel('A').point(lambda value: 255 if value > 20 else 0).getbbox()
    if box:
        logo = logo.crop(box)
    scale = min(target_w / logo.width, target_h / logo.height)
    logo = _resized(logo, scale)
    pad = 70
    width, height = logo.width + 2 * pad, logo.height + 2 * pad
    x = max(0, (cam_x - width) // 2)
    y = (H - height) // 2
    result = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(result).rounded_rectangle(
        (x, y, x + width, y + height), radius=44, fill=(255, 255, 255, 255)
    )
    result.alpha_composite(logo, (x + pad, y + pad))
    return result


def _pure_image(image, cam_x, mode):
    margin = CONTAIN_MARGIN if mode == 'contain' else COVER_MARGIN
    box_w, box_h = cam_x - 2 * margin, H - 2 * margin
    scale = fit_scale(image.width, image.height, box_w, box_h)
    image = _resized(image, scale)
    result = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    result.alpha_composite(image, ((cam_x - image.width) // 2, (H - image.height) // 2))
    return result


def build_layer(overlay):
    mode = _value(overlay, 'mode')
    image_path = _value(overlay, 'image')
    rect = _value(overlay, 'webcam_rect')
    cam_x = int(round(rect[0])) if rect else W
    image = _open(image_path) if image_path else None
    if mode == 'dark':
        result = Image.new('RGBA', (W, H), (8, 10, 16, 255))
    elif mode in ('panel_white', 'panel_dark'):
        color = (255, 255, 255, 255) if mode == 'panel_white' else (10, 12, 18, 255)
        result = Image.new('RGBA', (W, H), color)
        if image is not None:
            alpha_share = (np.asarray(image.getchannel('A')) < 250).mean()
            if alpha_share > .05:
                result.alpha_composite(_cutout(image, cam_x))
            else:
                scale = fit_scale(image.width, image.height, cam_x - 200, H - 200)
                image = _resized(image, scale)
                result.alpha_composite(image, ((cam_x - image.width) // 2, (H - image.height) // 2))
    elif mode == 'fill':
        scale = max(W / image.width, H / image.height)
        image = _resized(image.convert('RGB'), scale).convert('RGBA')
        left = (image.width - W) // 2
        top = max(0, (image.height - H) // 4)
        result = image.crop((left, top, left + W, top + H))
    elif mode == 'cutout':
        result = _cutout(image, cam_x, image_path)
    elif mode in ('badge', 'badge_big'):
        result = _badge(image, cam_x, *(BADGE_BIG if mode == 'badge_big' else BADGE))
    elif mode in ('cover', 'contain'):
        result = _pure_image(image, cam_x, mode)
    else:
        raise ValueError(f'Modo de layer desconhecido: {mode!r}.')
    return _punch_webcam(result, rect)


def save_layer(overlay, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    build_layer(overlay).save(path)
    return path
