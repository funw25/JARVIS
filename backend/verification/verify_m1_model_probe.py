"""Verify the M1 model adapter: refuses to invent quality, parses a real reply.

A local stub server stands in for a model endpoint so the plumbing can be tested
offline. Passing this check says the adapter works; it says nothing about model
quality, which stays unverified until a real model is configured.
"""
import json
import io
import os
import sys
import tempfile
import threading
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from usecases import m1_cli, m1_eval, m1_model

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
assert status["available"] is False and status["quality_measured"] is False, status
expect(m1_model.ModelNotConfigured, m1_model.engine, "engine without config")
expect(m1_model.ModelNotConfigured,
       lambda: m1_model.load_config({"JARVIS_M1_MODEL_PROVIDER": "ollama", "JARVIS_M1_MODEL": "x:cloud"}),
       "Ollama cloud model")
expect(m1_model.ModelNotConfigured,
       lambda: m1_model.load_config({"JARVIS_M1_MODEL_PROVIDER": "ollama", "JARVIS_M1_MODEL": "x:120b-cloud"}),
       "Ollama dash-cloud model")
expect(m1_model.ModelNotConfigured,
       lambda: m1_model.load_config({"JARVIS_M1_MODEL_PROVIDER": "ollama", "JARVIS_M1_MODEL": "local",
                                     "JARVIS_M1_MODEL_BASE_URL": "https://example.invalid"}),
       "non-local Ollama endpoint")
assert m1_model.is_loopback_endpoint(m1_model.ModelConfig("ollama", "local", "http://127.0.0.1:11434"))
assert not m1_model.is_loopback_endpoint(m1_model.ModelConfig("openai-compatible", "remote", "https://example.invalid/v1"))
assert m1_model.load_config({"JARVIS_M1_MODEL_PROVIDER": "ollama", "JARVIS_M1_MODEL": "local"}).base_url == "http://127.0.0.1:11434"
assert m1_eval.model_status()["status"] == "未验证"
print("MODEL_REFUSES_TO_RUN_UNCONFIGURED=PASS")

TEXT = "请确认下周三的演示时间是否仍然有效，我这边要订会议室。"


class Stub(BaseHTTPRequestHandler):
    mode = "ok"
    last_payload = None
    invalid_remaining = 0

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
        elif self.path.endswith("/api/tags"):
            self._send({"models": [
                {"name": "local-model", "size": 2500000000, "digest": "test-digest"},
                {"name": "x:120b-cloud", "size": 0, "digest": "cloud-digest"},
            ]})
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        Stub.last_payload = json.loads(self.rfile.read(length))
        if Stub.mode == "broken" or Stub.invalid_remaining:
            if Stub.invalid_remaining:
                Stub.invalid_remaining -= 1
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
        if Stub.mode == "unsupported":
            reply["evidence"] = ["not in the original message"]
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

    local = m1_model.probe(m1_model.ModelConfig("ollama", "local-model", "http://127.0.0.1:%d" % port))
    assert local["available"] is True and local["model_digest"] == "test-digest", local
    cloud = m1_model.probe(m1_model.ModelConfig("ollama", "x:120b-cloud", "http://127.0.0.1:%d" % port))
    assert cloud["available"] is False, cloud

    engine = m1_model.engine()
    suggestion = engine(TEXT, kind="email")
    assert suggestion.classification == "needs_reply"
    assert suggestion.priority == "normal"
    assert suggestion.wants_todo is True and suggestion.wants_draft is True
    assert suggestion.evidence and all(item["span"] in TEXT for item in suggestion.evidence), suggestion.evidence
    print("MODEL_REPLY_PARSED_WITH_VERBATIM_EVIDENCE=PASS")

    v2 = m1_model.engine(m1_model.ModelConfig("openai-compatible", "stub-model", base,
                                             prompt_version="v2"))
    assert v2(TEXT, kind="email").classification == "needs_reply"
    assert Stub.last_payload["messages"][0]["role"] == "system"
    assert Stub.last_payload["messages"][1] == {"role": "user", "content": TEXT}
    assert v2.name == "model:openai-compatible/stub-model"
    print("MODEL_V2_SEPARATES_INSTRUCTIONS_FROM_INPUT=PASS")

    row = {"id": "probe-1", "kind": "email", "text": TEXT,
           "labels": {"classification": "needs_reply", "priority": "normal", "todo": True, "draft": True}}
    report = m1_eval.evaluate_model([row], engine)
    assert report["engine"] == "model:openai-compatible/stub-model", report["engine"]
    assert report["details"][0]["evidence_supported"] is True
    original_rows = m1_cli._rows
    try:
        m1_cli._rows = lambda: [row]
        captured = io.StringIO()
        with tempfile.TemporaryDirectory(prefix="jarvis-m1-model-report-") as directory:
            report_path = Path(directory) / "report.json"
            with redirect_stdout(captured):
                assert m1_cli.command_eval(SimpleNamespace(engine="model", out=str(report_path))) == 0
            saved = json.loads(report_path.read_text(encoding="utf-8"))
            assert saved["complete_dataset"] is True
            assert len(saved["comparison"]) == 1 and saved["comparison"][0]["id"] == row["id"]
            assert saved["report"]["engine"] == "model:openai-compatible/stub-model"
            m1_cli._rows = lambda: [row, dict(row, id="probe-2")]
            with redirect_stdout(io.StringIO()):
                assert m1_cli.command_eval(SimpleNamespace(engine="model", out=str(report_path),
                                                           ids="probe-1")) == 0
            partial = json.loads(report_path.read_text(encoding="utf-8"))
            assert partial["complete_dataset"] is False and len(partial["comparison"]) == 1
        assert "engine: model:openai-compatible/stub-model" in captured.getvalue()
        assert "候选模型输出已测量" in captured.getvalue()
    finally:
        m1_cli._rows = original_rows
    os.environ["JARVIS_M1_MODEL_BASE_URL"] = "https://example.invalid/v1"
    assert m1_cli.command_eval(SimpleNamespace(engine="model", out=None, allow_remote=False)) == 2
    os.environ["JARVIS_M1_MODEL_BASE_URL"] = base
    Stub.mode = "unsupported"
    unsupported = m1_eval.evaluate_model([row], engine)
    assert unsupported["details"][0]["evidence_supported"] is False
    assert unsupported["evidence_verbatim_rate"] == 0.0
    Stub.mode = "ok"
    print("MODEL_REPORT_NAMES_ENGINE_AND_COUNTS_UNSUPPORTED_EVIDENCE=PASS")

    malformed = json.dumps({"classification": "fyi", "priority": "low", "todo": "false", "draft": False})
    expect(m1_model.ModelUnavailable, lambda: m1_model._parse_reply(malformed), "non-boolean action")
    malformed = json.dumps({"classification": "fyi", "priority": "urgent", "todo": False, "draft": False})
    expect(m1_model.ModelUnavailable, lambda: m1_model._parse_reply(malformed), "unknown priority")
    for blank in ("", "  ", "\t\n"):
        malformed = json.dumps({"classification": "action", "priority": "normal", "todo": True,
                                "draft": False, "evidence": [blank]})
        expect(m1_model.ModelUnavailable, lambda: m1_model._parse_reply(malformed), "blank evidence")

    Stub.mode = "broken"
    expect(m1_model.ModelUnavailable, lambda: engine(TEXT), "invalid model reply")
    Stub.mode = "ok"
    Stub.invalid_remaining = 1
    recovered = m1_eval.evaluate_model([row], engine)
    assert recovered["retry_items"] == 1 and recovered["retry_total"] == 1
    assert recovered["details"][0]["attempts"] == 2
    assert Stub.invalid_remaining == 0
    print("MODEL_BOUNDED_RETRY_IS_RECORDED=PASS")
    print("MODEL_INVALID_REPLY_IS_REJECTED=PASS")

    # The adapter running against a stub does not turn into a quality claim.
    assert m1_eval.model_status()["status"] == "未验证"
    expect(m1_eval.ModelUnavailable, lambda: m1_eval.evaluate_model([], None), "evaluation without a real model")
    print("MODEL_ADAPTER_DOES_NOT_CREATE_A_QUALITY_CLAIM=PASS")
finally:
    server.shutdown()
    for name in ("JARVIS_M1_MODEL_PROVIDER", "JARVIS_M1_MODEL", "JARVIS_M1_MODEL_BASE_URL",
                 "JARVIS_M1_MODEL_KEY_ENV", "JARVIS_M1_MODEL_TIMEOUT", "JARVIS_M1_PROMPT_VERSION"):
        os.environ.pop(name, None)

print("M1_MODEL_ADAPTER=PASS")
print("M1_MODEL_QUALITY=UNVERIFIED (no real model configured)")
