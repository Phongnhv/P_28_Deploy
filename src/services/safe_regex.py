from __future__ import annotations

from functools import lru_cache

import regex

MAX_PATTERN_LENGTH = 256
MAX_VALUE_LENGTH = 4096
MATCH_TIMEOUT_SECONDS = 0.025


class SafeRegexError(ValueError):
    pass


@lru_cache(maxsize=256)
def _compile(pattern: str) -> regex.Pattern:
    try:
        return regex.compile(pattern)
    except regex.error as exc:
        raise SafeRegexError("Regex pattern is invalid") from exc


def validate_regex(pattern: str) -> str:
    if not isinstance(pattern, str) or not pattern:
        raise SafeRegexError("Regex pattern is required")
    if len(pattern) > MAX_PATTERN_LENGTH:
        raise SafeRegexError(f"Regex pattern exceeds {MAX_PATTERN_LENGTH} characters")
    _compile(pattern)
    return pattern


def safe_search(pattern: str, value: object) -> bool:
    validate_regex(pattern)
    text = str(value)
    if len(text) > MAX_VALUE_LENGTH:
        raise SafeRegexError(f"Regex input exceeds {MAX_VALUE_LENGTH} characters")
    try:
        return _compile(pattern).search(text, timeout=MATCH_TIMEOUT_SECONDS) is not None
    except TimeoutError as exc:
        raise SafeRegexError("Regex evaluation timed out") from exc
