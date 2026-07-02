"""
Intelligence Operating System — Core Utilities
===============================================
Pure utility functions with no external I/O dependencies.

All functions here are:
  - Stateless (no side effects, no globals mutated)
  - Fully typed
  - Unit-testable in isolation
  - Importable without triggering heavy initialisation

Do NOT add anything here that requires DB, Redis, or LLM connections.
Those belong in the relevant service/repository modules.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
import uuid
from datetime import datetime, timezone
from typing import Any, TypeVar

T = TypeVar("T")


# ---------------------------------------------------------------------------
# UUID utilities
# ---------------------------------------------------------------------------


def is_valid_uuid(value: str) -> bool:
    """
    Check whether a string is a valid UUIDv4.

    Args:
        value: Candidate string.

    Returns:
        ``True`` if parseable as UUID, ``False`` otherwise.
    """
    try:
        uuid.UUID(value, version=4)
        return True
    except (ValueError, AttributeError):
        return False


def coerce_uuid(value: str | uuid.UUID) -> uuid.UUID:
    """
    Coerce a string or UUID to a ``uuid.UUID`` object.

    Args:
        value: String UUID or ``uuid.UUID``.

    Returns:
        ``uuid.UUID`` object.

    Raises:
        ValueError: ``value`` is not a valid UUID string.
    """
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def generate_uuid() -> uuid.UUID:
    """Return a new random UUIDv4."""
    return uuid.uuid4()


def generate_uuid_str() -> str:
    """Return a new random UUIDv4 as a hyphenated lowercase string."""
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Pagination envelope
# ---------------------------------------------------------------------------


def build_pagination_response(
    *,
    items: list[Any],
    total: int,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    """
    Construct a standard pagination envelope for collection API responses.

    Args:
        items: The page of items to return.
        total: Total number of items across all pages.
        page: Current 1-based page number.
        page_size: Number of items per page.

    Returns:
        Dictionary with ``items``, ``total``, ``page``, ``page_size``,
        ``pages``, ``has_next``, and ``has_prev`` keys.

    Example::

        return build_pagination_response(
            items=serialised_tasks,
            total=count,
            page=pagination.page,
            page_size=pagination.page_size,
        )
    """
    pages = max(1, math.ceil(total / page_size)) if total > 0 else 1
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
        "has_next": page < pages,
        "has_prev": page > 1,
    }


# ---------------------------------------------------------------------------
# Date / time utilities
# ---------------------------------------------------------------------------


def utcnow() -> datetime:
    """
    Return the current UTC time as a timezone-aware ``datetime`` object.

    Prefer this over ``datetime.utcnow()`` (which returns a naive datetime).

    Returns:
        Timezone-aware datetime in UTC.
    """
    return datetime.now(tz=timezone.utc)


def utcnow_isoformat() -> str:
    """
    Return the current UTC timestamp as an ISO 8601 string.

    Returns:
        e.g. ``"2024-01-15T12:34:56.789012+00:00"``
    """
    return utcnow().isoformat()


def datetime_to_timestamp(dt: datetime) -> float:
    """
    Convert a ``datetime`` to a POSIX timestamp (float seconds since epoch).

    Args:
        dt: Datetime object (naive datetimes are assumed UTC).

    Returns:
        POSIX timestamp as float.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


# ---------------------------------------------------------------------------
# String utilities
# ---------------------------------------------------------------------------


def slugify(text: str, *, max_length: int = 100) -> str:
    """
    Convert a string to a URL-safe slug.

    Args:
        text: Input string (e.g., a document title).
        max_length: Maximum slug length (truncates with trailing ``-`` removed).

    Returns:
        Lowercase, hyphen-delimited slug.

    Example::

        slugify("Hello World! This is a test.")
        # → "hello-world-this-is-a-test"
    """
    # Normalise unicode to ASCII approximations
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    # Replace non-alphanumeric chars with hyphens
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text[:max_length].rstrip("-")


def truncate(text: str, max_length: int, suffix: str = "...") -> str:
    """
    Truncate a string to ``max_length`` characters, appending ``suffix``.

    Args:
        text: Input string.
        max_length: Maximum total character length including suffix.
        suffix: String appended on truncation (default ``"..."``).

    Returns:
        Truncated string if ``len(text) > max_length``, else original.
    """
    if len(text) <= max_length:
        return text
    return text[: max_length - len(suffix)] + suffix


def sanitise_prompt_input(text: str) -> str:
    """
    Remove common prompt injection patterns from user input.

    This is a defence-in-depth measure; it is not a substitute for
    proper system prompt construction and output validation.

    Args:
        text: Raw user input string.

    Returns:
        Sanitised string with injection patterns neutralised.
    """
    # Strip null bytes
    text = text.replace("\x00", "")
    # Collapse excessive whitespace
    text = re.sub(r"\s{4,}", " ", text)
    # Remove common jailbreak prefixes (case-insensitive)
    patterns = [
        r"ignore\s+(all\s+)?previous\s+instructions",
        r"disregard\s+(all\s+)?prior\s+instructions",
        r"forget\s+(all\s+)?your\s+instructions",
        r"new\s+system\s+prompt",
        r"</?(system|instruction|prompt)>",
    ]
    for pattern in patterns:
        text = re.sub(pattern, "[FILTERED]", text, flags=re.IGNORECASE)
    return text.strip()


# ---------------------------------------------------------------------------
# Hashing utilities (non-security, deterministic)
# ---------------------------------------------------------------------------


def stable_hash(text: str) -> str:
    """
    Compute a stable, deterministic SHA-256 hash of a string.

    Useful for cache keys where the content determines the key
    (e.g., embedding cache keyed by text hash).

    Args:
        text: Input string.

    Returns:
        64-character hexadecimal SHA-256 digest.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_hash(obj: Any) -> str:
    """
    Compute a stable SHA-256 hash of any JSON-serialisable object.

    Args:
        obj: JSON-serialisable Python object.

    Returns:
        64-character hexadecimal SHA-256 digest.
    """
    serialised = json.dumps(obj, sort_keys=True, ensure_ascii=True, default=str)
    return stable_hash(serialised)


# ---------------------------------------------------------------------------
# JSON utilities
# ---------------------------------------------------------------------------


def safe_json_loads(text: str, default: Any = None) -> Any:
    """
    Parse JSON without raising an exception on failure.

    Args:
        text: JSON string to parse.
        default: Value to return on parse failure (default ``None``).

    Returns:
        Parsed Python object or ``default`` on error.
    """
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return default


def safe_json_dumps(obj: Any, *, indent: int | None = None) -> str:
    """
    Serialise an object to a JSON string, handling non-serialisable types.

    Non-serialisable values are coerced to their string representation via
    the ``default=str`` fallback.

    Args:
        obj: Python object to serialise.
        indent: JSON indentation (``None`` for compact).

    Returns:
        JSON string.
    """
    return json.dumps(obj, default=str, ensure_ascii=False, indent=indent)


# ---------------------------------------------------------------------------
# Chunking / batching utilities
# ---------------------------------------------------------------------------


def chunk_list(items: list[T], chunk_size: int) -> list[list[T]]:
    """
    Split a list into sub-lists of at most ``chunk_size`` elements.

    Args:
        items: Input list.
        chunk_size: Maximum size of each chunk (must be ≥ 1).

    Returns:
        List of sub-lists.

    Raises:
        ValueError: ``chunk_size`` is less than 1.

    Example::

        chunk_list([1, 2, 3, 4, 5], 2)
        # → [[1, 2], [3, 4], [5]]
    """
    if chunk_size < 1:
        raise ValueError(f"chunk_size must be >= 1, got {chunk_size}")
    return [items[i : i + chunk_size] for i in range(0, len(items), chunk_size)]


# ---------------------------------------------------------------------------
# Token estimation (rough)
# ---------------------------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """
    Estimate the number of tokens in a string using a rough heuristic.

    This uses the widely-cited rule of thumb: ~4 characters per token for
    English prose.  For precise counts, use the tokeniser of the target model.

    Args:
        text: Input string.

    Returns:
        Estimated token count (integer).
    """
    return max(1, len(text) // 4)


def fits_in_context(text: str, max_tokens: int) -> bool:
    """
    Check whether a text is estimated to fit within a token budget.

    Args:
        text: Input string.
        max_tokens: Maximum token count.

    Returns:
        ``True`` if estimated tokens ≤ ``max_tokens``.
    """
    return estimate_tokens(text) <= max_tokens


# ---------------------------------------------------------------------------
# Dict utilities
# ---------------------------------------------------------------------------


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """
    Deep-merge two dictionaries, with ``override`` taking precedence.

    Nested dicts are merged recursively; non-dict values are overwritten.

    Args:
        base: Base dictionary.
        override: Dictionary whose values override ``base``.

    Returns:
        New merged dictionary (neither input is mutated).
    """
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def flatten_dict(
    d: dict[str, Any],
    parent_key: str = "",
    sep: str = ".",
) -> dict[str, Any]:
    """
    Flatten a nested dictionary to a single level using dot-notation keys.

    Args:
        d: Input dictionary (possibly nested).
        parent_key: Prefix for current level (used in recursion).
        sep: Key separator (default ``"."``)

    Returns:
        Flat dictionary.

    Example::

        flatten_dict({"a": {"b": 1, "c": 2}, "d": 3})
        # → {"a.b": 1, "a.c": 2, "d": 3}
    """
    items: list[tuple[str, Any]] = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key, sep).items())
        else:
            items.append((new_key, v))
    return dict(items)
