"""Pure validation for the development server's local HTTP headers."""
from urllib.parse import urlsplit

_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}
_ALLOWED_ORIGINS = {"http://127.0.0.1:8765", "http://localhost:8765", "http://[::1]:8765"}


def local_header_error(hosts: list[str], origins: list[str], fetch_site: str) -> tuple[int, str] | None:
    if len(hosts) != 1 or len(origins) > 1:
        return (400, "Invalid local request headers")
    raw = hosts[0]
    if not raw or not raw.isascii() or any(ord(ch) <= 32 or ord(ch) == 127 for ch in raw) or any(ch in raw for ch in "@/\\?#%"):
        return (400, "Invalid Host header")
    try:
        parsed = urlsplit("//" + raw)
        host, port = parsed.hostname, parsed.port
    except ValueError:
        return (400, "Invalid Host header")
    if host not in _ALLOWED_HOSTS or (port is not None and port != 8765):
        return (400, "Local host only")
    if origins and origins[0] not in _ALLOWED_ORIGINS:
        return (403, "Origin not allowed")
    if fetch_site.lower() == "cross-site":
        return (403, "Cross-site request denied")
    return None


def has_ambiguous_singleton_headers(headers: list[tuple[bytes, bytes]]) -> bool:
    """Reject duplicated headers used for security, identity or body interpretation."""
    singleton = {b"authorization", b"x-jarvis-assistant", b"sec-fetch-site", b"content-type"}
    seen = set()
    for name, _ in headers:
        name = name.lower()
        if name in singleton:
            if name in seen:
                return True
            seen.add(name)
    return False
