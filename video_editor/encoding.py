"""Shared FFmpeg encoder probing and quality/bounded-bitrate arguments."""
import subprocess
from fractions import Fraction
from video_editor import media

REFERENCE_PIXEL_RATE = 1920 * 1080 * 60
MIN_MAX_BITRATE, MAX_MAX_BITRATE = 6_000_000, 80_000_000
DEFAULTS = dict(nvenc_cq=21, x264_crf=20, reference_max_bitrate=20_000_000)
_CACHE = {}


def encoder_available(name):
    """Encoder listed is not enough: encode a few frames to test the driver."""
    if name in _CACHE:
        return _CACHE[name]
    try:
        result = subprocess.run(
            [media.binary("ffmpeg"), "-hide_banner", "-v", "error", "-f", "lavfi",
             "-i", "color=c=black:s=256x256:r=30:d=0.2", "-c:v", name, "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        available = result.returncode == 0
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        available = False
    _CACHE[name] = available
    return available


def pick_encoder(preferred=None):
    if preferred:
        return preferred
    return "h264_nvenc" if encoder_available("h264_nvenc") else "libx264"


def max_bitrate(config, media_info):
    try:
        pixel_rate = float(media_info["width"]) * float(media_info["height"]) * float(media_info["fps"])
    except (KeyError, TypeError, ValueError):
        pixel_rate = REFERENCE_PIXEL_RATE
    scaled = config.get("reference_max_bitrate", DEFAULTS["reference_max_bitrate"]) * pixel_rate / REFERENCE_PIXEL_RATE
    return int(min(MAX_MAX_BITRATE, max(MIN_MAX_BITRATE, scaled)))


def encoder_args(codec, config, media_info, cpu_pix_fmt=False):
    cap = max_bitrate(config, media_info)
    rate = ["-maxrate", str(cap), "-bufsize", str(2 * cap)]
    if codec == "h264_nvenc":
        args = ["-c:v", codec, "-preset", "p5", "-tune", "hq", "-rc", "vbr",
                "-cq", str(config.get("nvenc_cq", DEFAULTS["nvenc_cq"])),
                "-b:v", str(int(cap * .7)), *rate, "-spatial-aq", "1", "-profile:v", "high"]
        return args + (["-pix_fmt", "yuv420p"] if cpu_pix_fmt else [])
    return ["-c:v", codec, "-threads:v", "4", "-preset", "fast",
            "-crf", str(config.get("x264_crf", DEFAULTS["x264_crf"])), *rate, "-pix_fmt", "yuv420p"]


def gpu_error(error):
    text = getattr(error, "stderr", None)
    if text is None:
        text = str(error)
    text = str(text).lower()
    return any(word in text for word in (
        "nvenc", "cuda", "nvidia", "no capable devices", "hwaccel",
        "hardware acceleration", "decoder surfaces", "impossible to convert",
    ))


def exact_timescale(fps):
    """MP4/MOV track timescale in which one frame is a whole number of ticks.

    60 fps -> 15360 (256 ticks per frame), 59.94 (60000/1001) -> 60000 (1001 ticks).
    The muxer's default for these streams (1/16000, or milliseconds in MKV) leaves
    frames at 266/267 ticks; copying packets out of such a file later produces
    non-monotonic timestamps at the joins.
    """
    rate = Fraction(float(fps)).limit_denominator(1001)
    if rate <= 0:
        return 15360
    return rate.numerator * (256 if rate.numerator < 1000 else 1)
