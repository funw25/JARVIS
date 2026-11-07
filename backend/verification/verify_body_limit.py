"""Exercise bounded request handling without network or third-party packages."""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.security.body_limit import RequestBodyLimitMiddleware


async def run_case(headers, messages, *, max_bytes=5, timeout=0.05):
    sent, observed = [], []
    queue = list(messages)
    async def receive():
        if queue:
            return queue.pop(0)
        await asyncio.sleep(1)
        return {"type": "http.disconnect"}
    async def send(message):
        sent.append(message)
    async def app(scope, inner_receive, inner_send):
        message = await inner_receive()
        observed.append(message["body"])
        await inner_send({"type": "http.response.start", "status": 200, "headers": []})
        await inner_send({"type": "http.response.body", "body": b"ok"})
    await RequestBodyLimitMiddleware(app, max_bytes=max_bytes, timeout_seconds=timeout)(
        {"type": "http", "headers": headers}, receive, send)
    return sent, observed


def body(data, more=False):
    return {"type": "http.request", "body": data, "more_body": more}


async def main():
    sent, seen = await run_case([(b"content-length", b"5")], [body(b"hello")])
    assert sent[0]["status"] == 200 and seen == [b"hello"]
    sent, seen = await run_case([], [body(b"ab", True), body(b"cd")])
    assert sent[0]["status"] == 200 and seen == [b"abcd"]
    sent, seen = await run_case([], [body(b"abc", True), body(b"def")])
    assert sent[0]["status"] == 413 and not seen
    assert (b"cache-control", b"no-store") in sent[0]["headers"]
    sent, seen = await run_case([(b"content-length", b"6")], [])
    assert sent[0]["status"] == 413 and not seen
    sent, _ = await run_case([(b"content-length", b"3")], [body(b"xx")])
    assert sent[0]["status"] == 400
    sent, _ = await run_case([(b"content-length", b"1"), (b"content-length", b"1")], [])
    assert sent[0]["status"] == 400
    sent, _ = await run_case([(b"content-length", b"-1")], [])
    assert sent[0]["status"] == 400
    sent, _ = await run_case([], [], timeout=0.01)
    assert sent[0]["status"] == 408
    sent, seen = await run_case([], [{"type": "http.disconnect"}])
    assert not sent and not seen

asyncio.run(main())
print("BODY_LIMIT_STREAMING_TIMEOUT_AND_HEADERS=PASS")
