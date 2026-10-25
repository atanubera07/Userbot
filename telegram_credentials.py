"""Parse Telegram app credentials without exposing their values in errors."""
import re
import unicodedata


_QUOTE_PAIRS = {"'": "'", '"': '"', '\u2018': '\u2019', '\u201c': '\u201d'}
_QUOTES = frozenset(_QUOTE_PAIRS) | frozenset(_QUOTE_PAIRS.values())


def _boundary_character(character: str) -> bool:
    return character.isspace() or unicodedata.category(character) == 'Cf'


def _clean_boundaries(value: str) -> str:
    start, end = 0, len(value)
    while start < end and _boundary_character(value[start]):
        start += 1
    while end > start and _boundary_character(value[end - 1]):
        end -= 1
    return value[start:end]


def normalize_credential(value: str | None) -> str:
    """Clean pasted boundaries and unwrap at most one matching quote pair.

    Interior characters are preserved, so malformed credentials cannot become
    valid merely by removing text, spaces, or hidden formatting between digits.
    """
    if value is None:
        return ''
    if not isinstance(value, str):
        raise ValueError('Credential must be text. In Render, enter only its value.')
    value = _clean_boundaries(value)
    if len(value) >= 2 and _QUOTE_PAIRS.get(value[0]) == value[-1]:
        value = _clean_boundaries(value[1:-1])
    return value


def _format_reason(value: str) -> str | None:
    if not value:
        return 'the value is empty'
    if '=' in value:
        return 'the value contains assignment text'
    if any(character in _QUOTES for character in value):
        return 'the value contains unmatched or extra quotation marks'
    if any(_boundary_character(character) for character in value):
        return 'the value contains interior whitespace or hidden formatting'
    return None


def parse_api_id(value: str | None) -> int:
    """Return a positive decimal API ID, with safe configuration errors."""
    value = normalize_credential(value)
    reason = _format_reason(value)
    if reason is None and not value.isdecimal():
        reason = 'the value contains non-decimal text'
    if reason is not None:
        raise ValueError(f'Invalid TELEGRAM_API_ID: {reason}. '
                         'In Render, enter only the API ID number, without a key or assignment.')
    try:
        api_id = int(value)
    except ValueError:
        raise ValueError('Invalid TELEGRAM_API_ID: the number is too long. '
                         'In Render, enter the API ID from my.telegram.org/apps.') from None
    if api_id <= 0:
        raise ValueError('TELEGRAM_API_ID must be a positive integer. '
                         'In Render, enter the API ID from my.telegram.org/apps.')
    return api_id


def parse_api_hash(value: str | None) -> str:
    """Return a 32-character hexadecimal API hash, preserving its case."""
    value = normalize_credential(value)
    reason = _format_reason(value)
    if reason is None and not re.fullmatch(r'[a-fA-F0-9]+', value):
        reason = 'the value contains non-hexadecimal text'
    if reason is None and len(value) != 32:
        reason = 'the value must contain exactly 32 hexadecimal characters'
    if reason is not None:
        raise ValueError(f'Invalid TELEGRAM_API_HASH: {reason}. '
                         'In Render, enter only the API hash, without a key or assignment.')
    return value
