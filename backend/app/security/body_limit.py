"""Bound request buffering for the local synthetic development API."""
import asyncio
import json
import time


class RequestBodyLimitMiddleware:
    """Buffer at most max_bytes before dispatch; not a production DoS defense."""
    def __init__(self, app, max_bytes: int = 1_048_576, timeout_seconds: float = 15.0):
        if type(max_bytes) is not int or max_bytes < 1 or timeout_seconds <= 0:
            raise ValueError("Invalid request limit configuration")
        self.app = app
        self.max_bytes = max_bytes
        self.timeout_seconds = timeout_seconds

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        lengths = [value for name, value in scope.get("headers", []) if name.lower() == b"content-length"]
        if len(lengths) > 1 or (lengths and (not lengths[0].isdigit() or len(lengths[0]) > 12)):
            await self._reject(send, 400, "Invalid Content-Length")
            return
        declared = int(lengths[0]) if lengths else None
        if declared is not None and declared > self.max_bytes:
            await self._reject(send, 413, "Request body too large")
            return
        buffer = bytearray()
        deadline = time.monotonic() + self.timeout_seconds
        chunks = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                await self._reject(send, 408, "Request body timed out")
                return
            try:
                message = await asyncio.wait_for(receive(), timeout=remaining)
            except asyncio.TimeoutError:
                await self._reject(send, 408, "Request body timed out")
                return
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request" or not isinstance(message.get("body", b""), bytes):
                await self._reject(send, 400, "Invalid request body")
                return
            chunks += 1
            data = message.get("body", b"")
            if chunks > 8192 or len(data) > self.max_bytes - len(buffer):
                await self._reject(send, 413, "Request body too large")
                return
            buffer.extend(data)
            if not message.get("more_body", False):
                break
        if declared is not None and declared != len(buffer):
            await self._reject(send, 400, "Content-Length mismatch")
            return
        first = True
        async def replay():
            nonlocal first
            if first:
                first = False
                return {"type": "http.request", "body": bytes(buffer), "more_body": False}
            return await receive()
        await self.app(scope, replay, send)

    @staticmethod
    async def _reject(send, status, detail):
        body = json.dumps({"detail": detail}, separators=(",", ":")).encode("ascii")
        await send({"type": "http.response.start", "status": status, "headers": [
            (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"no-referrer")]})
        await send({"type": "http.response.body", "body": body})
