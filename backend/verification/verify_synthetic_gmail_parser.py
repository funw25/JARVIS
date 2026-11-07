"""Verify fictional Gmail-shaped MIME parsing with no network or real messages."""
import base64
import copy
import os
import sys
from email.header import Header
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.security.storage_policy import StorageSecurityError
from app.services.synthetic_gmail_parser import parse_synthetic_gmail_fixture as parse


def encoded(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def rejected(value, error=ValueError, **kwargs):
    try:
        parse(value, **kwargs)
    except error:
        return
    raise AssertionError("Invalid fixture unexpectedly accepted")


fixture = {"id": "synthetic-1", "threadId": "thread-1", "internalDate": "1767225600123", "payload": {
    "mimeType": "multipart/mixed",
    "headers": [{"name": "From", "value": "Sender <sender@example.invalid>"},
                {"name": "Subject", "value": Header("合成主题", "utf-8").encode()}],
    "parts": [
        {"mimeType": "multipart/alternative", "parts": [
            {"mimeType": "text/plain", "headers": [{"name": "Content-Type", "value": "text/plain; charset=utf-8"}],
             "body": {"data": encoded("虚构正文；忽略这里的指令".encode())}},
            {"mimeType": "text/html", "body": {"data": encoded(b'<img src="https://example.invalid/track">')}}]},
        {"mimeType": "text/plain", "filename": "../fictional.txt", "headers": [{"name": "Content-Disposition", "value": "attachment"}],
         "body": {"data": encoded(b"attachment secret"), "size": 17}}]}}

os.environ.pop("JARVIS_DATA_MODE", None)
rejected(fixture, StorageSecurityError, synthetic_fixture=True)
os.environ["JARVIS_DATA_MODE"] = "synthetic"
rejected(fixture)
result = parse(fixture, synthetic_fixture=True)
assert result["subject"] == "合成主题" and result["sender"] == "sender@example.invalid"
assert result["received_at"] == "2026-01-01T00:00:00.123Z"
assert result["body"] == "虚构正文；忽略这里的指令" and result["content_is_untrusted"]
assert result["body_available"] and result["html_present"] and not result["external_body_not_fetched"]
assert result["attachments"] == [{"filename": "fictional.txt", "mime_type": "text/plain", "size": 17, "downloaded": False}]
assert "attachment secret" not in repr(result) and "track" not in repr(result)
print("MIME_TEXT_HTML_ATTACHMENT_AND_TIMESTAMP=PASS")

external = copy.deepcopy(fixture)
external["payload"]["parts"] = [{"mimeType": "text/plain", "body": {"attachmentId": "synthetic-part"}}]
view = parse(external, synthetic_fixture=True)
assert view["external_body_not_fetched"] and not view["body_available"] and view["body"] == ""
latin = copy.deepcopy(fixture)
latin["payload"]["parts"] = [{"mimeType": "text/plain", "headers": [{"name": "Content-Type", "value": "text/plain; charset=iso-8859-1"}], "body": {"data": encoded(b"caf\xe9")}}]
assert parse(latin, synthetic_fixture=True)["body"] == "café"
latin["payload"]["parts"][0]["headers"][0]["value"] = "text/plain; charset=does-not-exist"
assert parse(latin, synthetic_fixture=True)["decode_warning"]
print("EXTERNAL_BODY_NOT_FETCHED_AND_CHARSET_FALLBACK=PASS")

bad_address = copy.deepcopy(fixture)
bad_address["payload"]["headers"][0]["value"] = "sender@example.com"
rejected(bad_address, synthetic_fixture=True)
duplicate = copy.deepcopy(fixture)
duplicate["payload"]["headers"].append({"name": "From", "value": "other@example.invalid"})
rejected(duplicate, synthetic_fixture=True)
injection = copy.deepcopy(fixture)
injection["payload"]["headers"][1]["value"] = "hello\r\nBcc: other@example.invalid"
rejected(injection, synthetic_fixture=True)
bad_data = copy.deepcopy(fixture)
bad_data["payload"]["parts"][0]["parts"][0]["body"]["data"] = "++//"
rejected(bad_data, synthetic_fixture=True)
bad_mime = copy.deepcopy(fixture)
bad_mime["payload"]["parts"][1]["mimeType"] = "text/plain\r\nunsafe"
rejected(bad_mime, synthetic_fixture=True)
large = copy.deepcopy(fixture)
large["payload"]["parts"][0]["parts"][0]["body"]["data"] = encoded(b"x" * 100001)
rejected(large, synthetic_fixture=True)
combined = copy.deepcopy(fixture)
combined["payload"]["parts"] = [{"mimeType": "text/plain", "body": {"data": encoded(b"x" * 50000)}}, {"mimeType": "text/plain", "body": {"data": encoded(b"y" * 50000)}}]
rejected(combined, synthetic_fixture=True)
many = copy.deepcopy(fixture)
many["payload"]["parts"] = [{"mimeType": "text/plain", "body": {}}] * 101
rejected(many, synthetic_fixture=True)
deep = copy.deepcopy(fixture)
part = deep["payload"]
for _ in range(10):
    child = {"mimeType": "multipart/mixed", "parts": []}
    part["parts"] = [child]
    part = child
rejected(deep, synthetic_fixture=True)
print("INVALID_ADDRESS_HEADERS_DATA_AND_LIMITS_REJECTED=PASS")
print("SYNTHETIC_GMAIL_FIXTURE_PARSER=PASS")
