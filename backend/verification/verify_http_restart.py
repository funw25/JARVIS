"""Real local HTTP restart, owner isolation, and fail-closed storage checks."""
import http.client
import importlib.util
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
missing = [name for name in ("fastapi", "pydantic", "uvicorn") if importlib.util.find_spec(name) is None]
if missing:
    print("HTTP_RESTART_VERIFICATION=NOT_RUN (missing dependencies: " + ", ".join(missing) + ")")
    raise SystemExit(2)


def request(port, method, route, token, payload=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    headers = {"Host": "localhost:8765", "Authorization": "Bearer " + token, "X-Jarvis-Assistant": "jarvis"}
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    if body is not None:
        headers["Content-Type"] = "application/json"
    try:
        connection.request(method, "/api/v1" + route, body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        return response.status, json.loads(raw) if raw else None
    finally:
        connection.close()


def available_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def command(port):
    return [sys.executable, "-m", "uvicorn", "app.main:app", "--app-dir", str(ROOT / "backend"),
            "--host", "127.0.0.1", "--port", str(port), "--no-access-log"]


@contextmanager
def server(env, token, log):
    port = available_port()
    process = subprocess.Popen(command(port), cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError("Synthetic local server exited during startup")
            try:
                status, _ = request(port, "GET", "/health", token)
                if status == 200:
                    break
            except (OSError, http.client.HTTPException):
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError("Synthetic local server did not become ready")
        yield port
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


with tempfile.TemporaryDirectory(prefix="jarvis-http-restart-") as directory:
    temp = Path(directory)
    token = secrets.token_urlsafe(32)
    base = dict(os.environ, JARVIS_DATA_MODE="synthetic", JARVIS_DATA_DIR=str(temp / "data"), JARVIS_LOCAL_API_TOKEN=token)
    with (temp / "server.log").open("w+", encoding="utf-8") as log:
        with server(dict(base, JARVIS_OWNER_ID="owner-a"), token, log) as port:
            status, response = request(port, "POST", "/todos", token, {"title": "Fictional persistent task"})
            assert status == 201
            item_id = response["id"]
            status, task = request(port, "POST", "/todos", token, {"title": "Fictional reminder", "due_at": "2020-01-01T00:00:00Z"})
            assert status == 201
            status, reminder = request(port, "POST", "/jarvis/synthetic/reminders/rules", token, {"todo_id": task["id"], "offset_minutes": 0})
            assert status == 201
            reminder_id = reminder["id"]
            status, due = request(port, "GET", "/jarvis/synthetic/reminders/due", token)
            assert status == 200 and due["items"][0]["rule_id"] == reminder_id
            reminder_token = due["items"][0]["token"]
            status, message = request(port, "POST", "/jarvis/synthetic/mail", token, {"sender": "restart@example.invalid", "subject": "Fictional restart mail", "body": "fictional"})
            assert status == 201
            status, rule = request(port, "POST", "/jarvis/synthetic/mail-rules", token, {"name": "Restart rule", "sender_exact": "restart@example.invalid", "target_priority": "high"})
            assert status == 201
            assert request(port, "PATCH", "/jarvis/synthetic/mail-rules/" + rule["id"] + "/enabled", token, {"enabled": True})[0] == 200
            status, preview = request(port, "POST", "/jarvis/synthetic/mail-rules/" + rule["id"] + "/previews", token)
            assert status == 201
            preview_id, preview_hash = preview["id"], preview["plan_hash"]
        with server(dict(base, JARVIS_OWNER_ID="owner-b"), token, log) as port:
            assert request(port, "GET", "/todos/" + item_id, token)[0] == 404
            status, response = request(port, "GET", "/todos", token)
            assert status == 200 and all(item["id"] != item_id for item in response)
            assert request(port, "GET", "/jarvis/synthetic/mail-rule-previews/" + preview_id, token)[0] == 404
            assert request(port, "POST", "/jarvis/synthetic/reminders/" + reminder_id + "/acknowledge", token, {"token": reminder_token})[0] == 404
        with server(dict(base, JARVIS_OWNER_ID="owner-a"), token, log) as port:
            status, response = request(port, "GET", "/todos/" + item_id, token)
            assert status == 200 and response["title"] == "Fictional persistent task"
            status, response = request(port, "GET", "/jarvis/synthetic/mail-rule-previews/" + preview_id, token)
            assert status == 200 and response["status"] == "pending" and response["plan_hash"] == preview_hash
            status, response = request(port, "POST", "/jarvis/synthetic/mail-rule-previews/" + preview_id + "/apply", token, {"confirmed_hash": preview_hash})
            assert status == 200 and response["changed"] == 1
            status, response = request(port, "POST", "/jarvis/synthetic/reminders/" + reminder_id + "/acknowledge", token, {"token": reminder_token})
            assert status == 200 and not response["already_acknowledged"]
        with server(dict(base, JARVIS_OWNER_ID="owner-a"), token, log) as port:
            status, response = request(port, "GET", "/jarvis/synthetic/mail-rule-previews/" + preview_id, token)
            assert status == 200 and response["status"] == "applied" and "plan" not in response
            status, response = request(port, "GET", "/jarvis/synthetic/reminders/due", token)
            assert status == 200 and not response["items"]
            status, response = request(port, "POST", "/jarvis/synthetic/reminders/" + reminder_id + "/acknowledge", token, {"token": reminder_token})
            assert status == 200 and response["already_acknowledged"]
        invalid_env = dict(base, JARVIS_DATA_DIR=str(temp / "must-not-exist"))
        invalid_env.pop("JARVIS_DATA_MODE", None)
        run = subprocess.run(command(available_port()), cwd=ROOT, env=invalid_env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=15)
        assert run.returncode != 0 and b"StorageSecurityError" in run.stdout
        assert not (temp / "must-not-exist").exists()
print("HTTP_RESTART_OWNER_ISOLATION_AND_STORAGE_FAIL_CLOSED=PASS")
