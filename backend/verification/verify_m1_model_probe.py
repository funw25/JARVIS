"""Verify the M1 model adapter: refuses to invent quality, parses a real reply.

A local stub server stands in for a model endpoint so the plumbing can be tested
offline. Passing this check says the adapter works; it says nothing about model
quality, which stays unverified until a real model is configured.
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from usecases import m1_eval, m1_model

for name in ("JARVIS_M1_MODEL_PROVIDER", "JARVIS_M1_MODEL", "JARVIS_M1_MODEL_BASE_URL",
             "JARVIS_M1_MODEL_KEY_ENV", "JARVIS_M1_MODEL_TIMEOUT"):
    os.environ.pop(name, None)


def expect(error, call, label):
    try:
        call()
    except error:
        return
    except Exception as exc:  # noqa: BLE001
        raise AssertionError("%s raised %s instead of %s" % (label, type(exc).__name__, error.__name__)) from exc
    raise AssertionError("%s did not raise %s" % (label, error.__name__))


# 1. No configuration: the adapter must refuse rather than return a default.
assert m1_model.load_config() is None
status = m1_model.probe()
assert status["available"] is False and status["measured"] is False, status
expect(m1_model.ModelNotConfigured, m1_model.engine, "engine without config")
assert m1_eval.model_status()["status"] == "未验证"
print("MODEL_REFUSES_TO_RUN_UNCONFIGURED=PASS")

TEXT = "请确认下周三的演示时间是否仍然有效，我这边要订会议室。"


class Stub(BaseHTTPRequestHandler):
    mode = "ok"

    def log_message(self, *args):  # silence the test output
        pass

    def _send(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.endswith("/models"):
            self._send({"data": [{"id": "stub-model"}]})
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        if Stub.mode == "broken":
            payload = "this is not json"
            body = json.dumps({"choices": [{"message": {"content": payload}}]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        reply = {
            "classification": "needs_reply",
            "priority": "normal",
            "todo": True,
            "draft": True,
            "evidence": ["请确认下周三的演示时间"],
            "confidence": 0.8,
        }
        self._send({"choices": [{"message": {"content": json.dumps(reply, ensure_ascii=False)}}]})


server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()
base = "http://127.0.0.1:%d/v1" % port

try:
    os.environ["JARVIS_M1_MODEL_PROVIDER"] = "openai-compatible"
    os.environ["JARVIS_M1_MODEL"] = "stub-model"
    os.environ["JARVIS_M1_MODEL_BASE_URL"] = base
    os.environ["JARVIS_M1_MODEL_TIMEOUT"] = "5"

    status = m1_model.probe()
    assert status["available"] is True, status
    print("MODEL_PROBE_FINDS_LOCAL_ENDPOINT=PASS models=%s" % status.get("models"))

    engine = m1_model.engine()
    suggestion = engine(TEXT, kind="email")
    assert suggestion.classification == "needs_reply"
    assert suggestion.priority == "normal"
    assert suggestion.wants_todo is True and suggestion.wants_draft is True
    assert suggestion.evidence and all(item["span"] in TEXT for item in suggestion.evidence), suggestion.evidence
    print("MODEL_REPLY_PARSED_WITH_VERBATIM_EVIDENCE=PASS")

    Stub.mode = "broken"
    expect(m1_model.ModelUnavailable, lambda: engine(TEXT), "invalid model reply")
    Stub.mode = "ok"
    print("MODEL_INVALID_REPLY_IS_REJECTED=PASS")

    # The adapter running against a stub does not turn into a quality claim.
    assert m1_eval.model_status()["status"] == "未验证"
    expect(m1_eval.ModelUnavailable, lambda: m1_eval.evaluate_model([], None), "evaluation without a real model")
    print("MODEL_ADAPTER_DOES_NOT_CREATE_A_QUALITY_CLAIM=PASS")
finally:
    server.shutdown()
    for name in ("JARVIS_M1_MODEL_PROVIDER", "JARVIS_M1_MODEL", "JARVIS_M1_MODEL_BASE_URL",
                 "JARVIS_M1_MODEL_KEY_ENV", "JARVIS_M1_MODEL_TIMEOUT"):
        os.environ.pop(name, None)

print("M1_MODEL_ADAPTER=PASS")
print("M1_MODEL_QUALITY=UNVERIFIED (no real model configured)")
