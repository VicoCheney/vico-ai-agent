"""Shared text utilities for token estimation.

Centralises CJK character counting and token estimation so that
ContextManager and PromptLoader share a single implementation.
"""

from __future__ import annotations

# CJK code-point ranges used for token estimation.
# Each tuple is (inclusive_low, inclusive_high).
_CJK_RANGES: tuple[tuple[int, int], ...] = (
    (0x4E00, 0x9FFF),   # CJK Unified Ideographs
    (0x3400, 0x4DBF),   # CJK Extension A
    (0x20000, 0x2A6DF), # CJK Extension B
    (0x3040, 0x30FF),   # Hiragana + Katakana
    (0xAC00, 0xD7AF),   # Hangul Syllables
    (0xFF00, 0xFFEF),   # Full-width forms
)

_CHARS_PER_TOKEN = 4        # ASCII / Latin text
_CJK_CHARS_PER_TOKEN = 1.5  # CJK ideographs


def count_cjk(text: str) -> int:
    """Count CJK / Hiragana / Katakana / Hangul code points in *text*.

    Counting via code-point ranges is faster than calling ``unicodedata``
    for every character and is accurate enough for token-budget estimation.
    """
    n = 0
    for ch in text:
        cp = ord(ch)
        for lo, hi in _CJK_RANGES:
            if lo <= cp <= hi:
                n += 1
                break
    return n


def estimate_tokens(text: str) -> int:
    """Rough token count: ASCII ~4 chars/token, CJK ~1.5 chars/token.

    Returns at least 1 for any non-empty input.
    """
    if not text:
        return 0
    cjk = count_cjk(text)
    other = len(text) - cjk
    return max(1, int(cjk / _CJK_CHARS_PER_TOKEN) + other // _CHARS_PER_TOKEN)
