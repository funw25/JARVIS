"""Validation for the temporary local development bearer token."""
import hmac
import re

_TOKEN = re.compile(r"[A-Za-z0-9_-]{32,128}\Z")


def valid_development_token(value: object) -> bool:
    return isinstance(value, str) and _TOKEN.fullmatch(value) is not None


def matches_development_token(expected: object, supplied: object) -> bool:
    if not valid_development_token(expected) or not valid_development_token(supplied):
        return False
    return hmac.compare_digest(expected, supplied)
