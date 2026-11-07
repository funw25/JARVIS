"""Local HTTP header validation unit checks; not an HTTP integration test."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.security.request_guard import local_header_error

for host, origin in (("127.0.0.1:8765", []), ("localhost:8765", ["http://localhost:8765"]), ("[::1]:8765", ["http://[::1]:8765"])):
    assert local_header_error([host], origin, "same-origin") is None
for hosts, origins, fetch in (([], [], ""), (["localhost", "evil"], [], ""), (["user@localhost:8765"], [], ""), (["localhost:8765/path"], [], ""), (["localhost:9999"], [], ""), (["evil.example:8765"], [], ""), (["localhost:8765"], ["null"], ""), (["localhost:8765"], ["http://localhost:8765", "http://evil"], ""), (["localhost:8765"], [], "cross-site"), (["localhost:8765\r\nX-Test: a"], [], "")):
    assert local_header_error(hosts, origins, fetch) is not None, (hosts, origins, fetch)
print("LOCAL_HEADER_VALIDATION=PASS")
print("HTTP_RUNTIME=NOT_TESTED")
