"""Parse fictional Gmail-shaped JSON without network, attachment fetches or HTML rendering.

This is an offline fixture parser, not a Gmail connector. Callers must not pass
real emails; the synthetic flag cannot classify the contents of a message.
"""
import base64
import binascii
import re
from datetime import datetime, timezone
from email.errors import HeaderParseError
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr

from ..security.storage_policy import require_synthetic_storage
from .synthetic_mail_service import _address

_ID = re.compile(r"[A-Za-z0-9_-]{1,100}\Z")
_MIME = re.compile(r"[A-Za-z0-9!#$&^_.+-]{1,100}/[A-Za-z0-9!#$&^_.+-]{1,100}\Z")
_MAX_PARTS = 100
_MAX_DEPTH = 8
_MAX_BODY_BYTES = 100_000


def _identifier(value: object) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise ValueError("Invalid synthetic message identifier")
    return value


def _headers(part: dict) -> dict[str, list[str]]:
    headers = part.get("headers", [])
    if not isinstance(headers, list) or len(headers) > 100:
        raise ValueError("Invalid synthetic message headers")
    result: dict[str, list[str]] = {}
    for header in headers:
        if not isinstance(header, dict):
            raise ValueError("Invalid synthetic message header")
        name, value = header.get("name"), header.get("value")
        if not isinstance(name, str) or not name.isascii() or not re.fullmatch(r"[A-Za-z0-9-]{1,100}", name):
            raise ValueError("Invalid synthetic header name")
        if not isinstance(value, str) or len(value) > 4096 or any(ch in value for ch in "\r\n\x00"):
            raise ValueError("Invalid synthetic header value")
        result.setdefault(name.lower(), []).append(value)
    return result


def _one(headers: dict[str, list[str]], name: str, *, required: bool = False) -> str:
    values = headers.get(name, [])
    if len(values) > 1 or (required and not values):
        raise ValueError("Missing or ambiguous synthetic message header")
    return values[0] if values else ""


def _subject(value: str) -> str:
    try:
        text = str(make_header(decode_header(value))).strip()
    except (HeaderParseError, LookupError, UnicodeError, ValueError) as exc:
        raise ValueError("Invalid synthetic subject encoding") from exc
    if not text or len(text) > 500 or any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise ValueError("Invalid synthetic message subject")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("Invalid synthetic subject text") from exc
    return text


def _timestamp(value: object) -> str:
    if not isinstance(value, str) or not value.isascii() or not value.isdigit() or len(value) > 15:
        raise ValueError("Invalid synthetic message timestamp")
    try:
        seconds, milliseconds = divmod(int(value), 1000)
        return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(microsecond=milliseconds * 1000).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError("Invalid synthetic message timestamp") from exc


def _plain_data(value: object, remaining: int) -> bytes:
    if not isinstance(value, str) or not value.isascii() or re.fullmatch(r"[A-Za-z0-9_-]*={0,2}", value) is None or len(value) > ((remaining + 2) // 3) * 4 + 4:
        raise ValueError("Invalid or oversized synthetic part data")
    try:
        data = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Invalid synthetic base64url data") from exc
    if len(data) > remaining:
        raise ValueError("Synthetic message body exceeds limit")
    return data


def _decode_text(data: bytes, headers: dict[str, list[str]]) -> tuple[str, bool]:
    content_type = _one(headers, "content-type")
    message = Message()
    message["content-type"] = content_type or "text/plain; charset=utf-8"
    charset = message.get_content_charset() or "utf-8"
    try:
        return data.decode(charset, errors="strict"), False
    except (LookupError, UnicodeError):
        return data.decode("utf-8", errors="replace"), True


def parse_synthetic_gmail_fixture(message: dict, *, synthetic_fixture: bool = False) -> dict:
    """Return bounded plain text and attachment metadata from an explicitly fictional fixture."""
    require_synthetic_storage()
    if synthetic_fixture is not True:
        raise ValueError("Explicit synthetic fixture confirmation is required")
    if not isinstance(message, dict):
        raise ValueError("Invalid synthetic Gmail-shaped fixture")
    message_id, thread_id = _identifier(message.get("id")), _identifier(message.get("threadId"))
    received_at = _timestamp(message.get("internalDate"))
    root = message.get("payload")
    if not isinstance(root, dict):
        raise ValueError("Synthetic payload is required")
    top_headers = _headers(root)
    from_header, subject_header = _one(top_headers, "from", required=True), _one(top_headers, "subject", required=True)
    _, sender = parseaddr(from_header)
    sender, subject = _address(sender), _subject(subject_header)
    parts, pending = [], [(root, 0, False)]
    plain_chunks, attachments = [], []
    byte_count, scheduled = 0, 1
    decode_warning, html_only, external_body = False, False, False
    while pending:
        part, depth, inherited_attachment = pending.pop()
        if not isinstance(part, dict) or depth > _MAX_DEPTH:
            raise ValueError("Synthetic MIME structure exceeds limits")
        parts.append(part)
        if len(parts) > _MAX_PARTS:
            raise ValueError("Too many synthetic MIME parts")
        headers = _headers(part)
        mime = part.get("mimeType", "application/octet-stream")
        filename = part.get("filename", "")
        body = part.get("body", {})
        children = part.get("parts", [])
        if not isinstance(mime, str) or _MIME.fullmatch(mime) is None or not isinstance(filename, str) or len(filename) > 500 or not isinstance(body, dict) or not isinstance(children, list) or len(children) > _MAX_PARTS:
            raise ValueError("Invalid synthetic MIME part")
        disposition = _one(headers, "content-disposition").split(";", 1)[0].strip().lower()
        attached = inherited_attachment or bool(filename) or disposition == "attachment"
        if attached and not inherited_attachment:
            try:
                filename.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ValueError("Invalid synthetic attachment filename") from exc
            safe_name = filename.replace("\\", "/").rsplit("/", 1)[-1]
            safe_name = "".join(ch for ch in safe_name if ord(ch) >= 32 and ord(ch) != 127)[:200]
            size = body.get("size")
            if size is not None and (type(size) is not int or size < 0 or size > 1_000_000_000):
                raise ValueError("Invalid synthetic attachment size")
            attachments.append({"filename": safe_name, "mime_type": mime.lower(), "size": size, "downloaded": False})
        if not attached and mime.lower() == "text/plain":
            if body.get("attachmentId"):
                external_body = True
            elif "data" in body:
                separator = 1 if plain_chunks else 0
                if byte_count + separator > _MAX_BODY_BYTES:
                    raise ValueError("Synthetic message body exceeds limit")
                raw = _plain_data(body["data"], _MAX_BODY_BYTES - byte_count - separator)
                byte_count += len(raw) + separator
                text, warned = _decode_text(raw, headers)
                plain_chunks.append(text)
                decode_warning = decode_warning or warned
        elif not attached and mime.lower() == "text/html":
            html_only = True
        scheduled += len(children)
        if scheduled > _MAX_PARTS:
            raise ValueError("Too many synthetic MIME parts")
        for child in reversed(children):
            pending.append((child, depth + 1, attached))
    return {"synthetic": True, "external_id": message_id, "thread_id": thread_id, "sender": sender,
            "subject": subject, "received_at": received_at, "body": "\n".join(plain_chunks),
            "body_available": bool(plain_chunks), "html_present": html_only, "external_body_not_fetched": external_body,
            "decode_warning": decode_warning, "attachments": attachments, "content_is_untrusted": True}
