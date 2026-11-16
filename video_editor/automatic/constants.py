"""Automatic engine constants kept stable for public callers."""
POLICY_VERSION = 3
CONTEXT_SUFFIX = " · Contexto"
CONTEXT_UNAVAILABLE_SUFFIX = " · Contexto indisponível"
NEW_CLASSES = (
    "retake",
    "false_start",
    "correction",
    "adaptive_silence",
    "contextual_restart",
)
ACTIVE_STATES = (
    "queued",
    "preparing",
    "analyzing",
    "analyzing_takes",
    "validating",
    "checking_sequence",
    "exporting",
)
