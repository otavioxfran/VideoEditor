"""Public automatic-editing API."""
import subprocess  # noqa: F401

from .constants import ACTIVE_STATES, CONTEXT_SUFFIX, CONTEXT_UNAVAILABLE_SUFFIX, NEW_CLASSES, POLICY_VERSION
from .bounds import overlap, safe_bounds
from .context import audio_context, refine_visual, visual_context
from .classify import classify
from .editorial import editorial_packet, validate_editorial
from .voice import background_voice_candidates
from .runner import Automatic

__all__ = [
    "ACTIVE_STATES", "Automatic", "CONTEXT_SUFFIX", "CONTEXT_UNAVAILABLE_SUFFIX",
    "NEW_CLASSES", "POLICY_VERSION", "audio_context", "background_voice_candidates",
    "classify", "editorial_packet", "overlap", "refine_visual", "safe_bounds",
    "validate_editorial", "visual_context",
]
