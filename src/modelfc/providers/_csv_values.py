"""Shared CSV value validation; source filtering stays in each adapter."""

import re


_WHOLE_NUMBER = re.compile(r"[0-9]+(?:\.0+)?")


def required_text(row: dict[str, str | None], field: str) -> str:
    """Read and trim a required field, rejecting absent or blank values."""
    value = row[field]
    if value is None or not value.strip():
        raise ValueError(f"{field} is required")
    return value.strip()


def parse_whole_number(value: str | None, field: str) -> int:
    """Parse ASCII non-negative integers or whole decimals without floats."""
    stripped = "" if value is None else value.strip()
    if _WHOLE_NUMBER.fullmatch(stripped) is None:
        raise ValueError(
            f"{field} must be a non-negative whole number: {stripped!r}"
        )
    return int(stripped.split(".", 1)[0])
