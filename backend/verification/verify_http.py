"""Run real loopback HTTP checks with a temporary synthetic-only database.

Requires this interpreter to have the project's FastAPI, Pydantic and Uvicorn
requirements installed. Does not connect external accounts or use personal data.
"""
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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
required = ("fastapi", "pydantic", "uvicorn")
missing = [name for name in required if importlib.util.find_spec(name) is None]
if missing:
    print("HTTP_VERIFICATION=NOT_RUN (missing dependencies: " + ", ".join(missing) + ")")
    raise SystemExit(2)


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def request(port, method, route, payload=None, headers=None, host="localhost:8765"):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        conn.putrequest(method, "/api/v1" + route, skip_host=True)
        conn.putheader("Host", host)
        pairs = headers.items() if hasattr(headers, "items") else (headers or [])
        for key, value in pairs:
            conn.putheader(key, value)
        if body is not None:
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(len(body)))
        conn.endheaders(body)
        response = conn.getresponse()
        raw = response.read()
        return response.status, dict(response.getheaders()), json.loads(raw) if raw else None
    finally:
        conn.close()


with tempfile.TemporaryDirectory(prefix="jarvis-http-check-") as directory:
    # Bind a free port for the test. The guard checks the explicit Host header;
    # no browser or external interface is used.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    token = secrets.token_urlsafe(32)
    env = dict(os.environ, JARVIS_DATA_MODE="synthetic", JARVIS_DATA_DIR=directory,
               JARVIS_LOCAL_API_TOKEN=token, JARVIS_OWNER_ID="http-test-owner")
    command = [sys.executable, "-m", "uvicorn", "app.main:app", "--app-dir", str(ROOT / "backend"),
               "--host", "127.0.0.1", "--port", str(port), "--no-access-log"]
    log_path = Path(directory) / "server.log"
    with log_path.open("w+", encoding="utf-8") as log:
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            for attempt in range(100):
                if process.poll() is not None:
                    log.flush()
                    raise RuntimeError("Local test server failed to start; inspect test server output")
                try:
                    status, _, health = request(port, "GET", "/health")
                    if status == 200:
                        break
                except (OSError, http.client.HTTPException):
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError("Local test server did not become ready")
            check(health.get("data_mode") == "synthetic-only", "health data mode")
            auth = {"Authorization": "Bearer " + token, "X-Jarvis-Assistant": "jarvis"}
            check(request(port, "GET", "/todos")[0] == 401, "missing token accepted")
            check(request(port, "GET", "/todos", headers={**auth, "Authorization": "Bearer " + "x" * 40})[0] == 401, "wrong token accepted")
            duplicate_authorization = list(auth.items()) + [("authorization", "Bearer " + token)]
            check(request(port, "GET", "/todos", headers=duplicate_authorization)[0] == 400, "duplicate authorization accepted")
            duplicate_assistant = list(auth.items()) + [("x-jarvis-assistant", "companion")]
            check(request(port, "GET", "/todos", headers=duplicate_assistant)[0] == 400, "duplicate assistant accepted")
            duplicate_content_type = list(auth.items()) + [("Content-Type", "application/json"), ("content-type", "text/plain")]
            check(request(port, "GET", "/todos", headers=duplicate_content_type)[0] == 400, "duplicate content type accepted")
            duplicate_fetch = list(auth.items()) + [("Sec-Fetch-Site", "same-origin"), ("sec-fetch-site", "cross-site")]
            check(request(port, "GET", "/todos", headers=duplicate_fetch)[0] == 400, "duplicate fetch site accepted")
            check(request(port, "GET", "/todos", headers=auth, host="example.com")[0] == 400, "external host accepted")
            blocked_status, blocked_headers, _ = request(port, "GET", "/todos", headers={**auth, "Origin": "https://example.com"})
            check(blocked_status == 403 and blocked_headers.get("cache-control") == "no-store", "external origin accepted or rejection cacheable")
            check(request(port, "GET", "/todos", headers={**auth, "Sec-Fetch-Site": "cross-site"})[0] == 403, "cross-site request accepted")
            oversized = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            try:
                oversized.putrequest("POST", "/api/v1/todos", skip_host=True)
                oversized.putheader("Host", "localhost:8765")
                oversized.putheader("Authorization", auth["Authorization"])
                oversized.putheader("X-Jarvis-Assistant", "jarvis")
                oversized.putheader("Content-Length", "1048577")
                oversized.endheaders()
                response = oversized.getresponse()
                check(response.status == 413 and response.getheader("Cache-Control") == "no-store", "oversized request accepted or cacheable")
                response.read()
            finally:
                oversized.close()
            check(request(port, "POST", "/todos", {"title": "Out of range", "due_at": "0001-01-01T00:00:00+14:00"}, auth)[0] == 422, "out of range timestamp accepted or crashed")
            status, _, extreme = request(port, "POST", "/todos", {"title": "Far future", "due_at": "9999-12-31T23:59:59Z"}, auth)
            check(status == 201 and request(port, "GET", "/jarvis/overview?utc_offset_minutes=840", headers=auth)[0] == 200, "far future task broke overview")
            check(request(port, "DELETE", "/todos/" + extreme["id"], headers=auth)[0] == 200, "extreme test cleanup failed")
            check(request(port, "POST", "/todos", {"title": "\ud800"}, auth)[0] == 422, "invalid Unicode caused error or was accepted")
            check(request(port, "POST", "/todos", {"title": "valid", "sources": [{"type": "email", "id": "\ud800"}]}, auth)[0] == 422, "nested invalid Unicode caused error or was accepted")
            check(request(port, "POST", "/todos", {"title": "valid", "\ud800": "extra"}, auth)[0] == 422, "invalid Unicode key caused error or was accepted")
            secret_value = "fictional-private-value-" * 30
            invalid_status, _, invalid_response = request(port, "POST", "/todos", {"title": secret_value}, auth)
            check(invalid_status == 422 and secret_value not in json.dumps(invalid_response), "invalid request echoed input")
            check(request(port, "POST", "/todos", {"title": "sample", "owner_id": "other"}, auth)[0] == 422, "extra owner accepted")
            check(request(port, "POST", "/todos", {"title": "sample"}, {"Authorization": auth["Authorization"]})[0] == 422, "missing assistant accepted")
            status, headers, result = request(port, "POST", "/todos", {"title": "Synthetic task"}, auth)
            check(status == 201 and isinstance(result.get("id"), str), "todo creation failed")
            check(headers.get("cache-control") == "no-store", "no-store header missing")
            todo_id = result["id"]
            keyed_auth = {**auth, "Idempotency-Key": "synthetic-request-1"}
            first = request(port, "POST", "/todos", {"title": "Retry task"}, keyed_auth)
            second = request(port, "POST", "/todos", {"title": "Retry task"}, keyed_auth)
            check(first[0] == second[0] == 201 and first[2]["id"] == second[2]["id"], "idempotent retry created another todo")
            check(request(port, "POST", "/todos", {"title": "Different task"}, keyed_auth)[0] == 409, "idempotency key conflict accepted")
            duplicate = list(auth.items()) + [("Idempotency-Key", "a"), ("Idempotency-Key", "b")]
            check(request(port, "POST", "/todos", {"title": "Duplicate header"}, duplicate)[0] == 422, "duplicate idempotency header accepted")
            status, _, view = request(port, "GET", "/jarvis/overview", headers=auth)
            check(status == 200 and view["todos"]["open"] >= 2, "Jarvis overview missing tasks")
            status, _, results = request(port, "GET", "/jarvis/search?query=Synthetic", headers=auth)
            check(status == 200 and any(row["id"] == todo_id for row in results["items"]), "unified search did not find task")
            status, _, results = request(port, "GET", "/jarvis/tasks?query=Synthetic&limit=1", headers=auth)
            check(status == 200 and results["items"][0]["id"] == todo_id, "task search did not find task")
            check(request(port, "GET", "/jarvis/tasks?limit=101", headers=auth)[0] == 422, "task page limit ignored")
            check(request(port, "GET", "/jarvis/search?query=Synthetic&kind=unknown", headers=auth)[0] == 422, "unknown search kind accepted")
            check(request(port, "GET", "/jarvis/overview", headers={**auth, "X-Jarvis-Assistant": "companion"})[0] == 403, "companion read Jarvis overview")
            status, _, result = request(port, "GET", "/jarvis/mail/threads", headers=auth)
            check(status == 200 and result["items"] == [], "empty synthetic mail threads failed")
            mail_payload = {"sender": "sender@example.invalid", "subject": "Fixture mail", "body": "synthetic private mail body"}
            check(request(port, "POST", "/jarvis/synthetic/mail", {**mail_payload, "sender": "real@example.com"}, auth)[0] == 422, "real address accepted")
            check(request(port, "POST", "/jarvis/synthetic/mail", {**mail_payload, "owner_id": "other"}, auth)[0] == 422, "mail owner spoof accepted")
            check(request(port, "POST", "/jarvis/synthetic/mail", mail_payload, {**auth, "X-Jarvis-Assistant": "companion"})[0] == 403, "companion created Jarvis mail")
            status, _, created_mail = request(port, "POST", "/jarvis/synthetic/mail", mail_payload, auth)
            check(status == 201 and created_mail["synthetic"], "synthetic mail creation failed")
            mail_id = created_mail["id"]
            status, _, mail_item = request(port, "GET", "/jarvis/synthetic/mail/" + mail_id, headers=auth)
            check(status == 200 and mail_item["body"] == mail_payload["body"] and "owner_id" not in mail_item, "synthetic mail detail failed")
            check(request(port, "PATCH", "/jarvis/synthetic/mail/" + mail_id + "/triage", {"triage_status": "needs_reply", "priority": "high"}, auth)[0] == 200, "synthetic mail triage failed")
            status, _, mail_threads = request(port, "GET", "/jarvis/mail/threads", headers=auth)
            check(status == 200 and len(mail_threads["items"]) == 1 and mail_threads["items"][0]["latest_mail_id"] == mail_id and "synthetic private mail body" not in repr(mail_threads), "synthetic mail thread summary failed")
            mail_task_path = "/jarvis/synthetic/mail/" + mail_id + "/tasks"
            source_auth = {**auth, "Idempotency-Key": "synthetic-mail-task"}
            first_task = request(port, "POST", mail_task_path, {"title": "Reply to fixture"}, source_auth)
            second_task = request(port, "POST", mail_task_path, {"title": "Reply to fixture"}, source_auth)
            check(first_task[0] == second_task[0] == 201 and first_task[2]["id"] == second_task[2]["id"], "mail-to-task retry duplicated")
            status, _, source_task = request(port, "GET", "/todos/" + first_task[2]["id"], headers=auth)
            check(status == 200 and source_task["sources"][0]["source_id"] == mail_id, "mail task source missing")
            draft_payload = {"account": "me@example.invalid", "recipients": ["sender@example.invalid"], "subject": "Re: Fixture", "body": "fictional reply"}
            status, _, draft = request(port, "POST", "/jarvis/synthetic/mail/" + mail_id + "/drafts", draft_payload, auth)
            check(status == 201 and draft["synthetic"], "draft creation failed")
            draft_id = draft["id"]
            proposal_path = "/jarvis/synthetic/drafts/" + draft_id + "/send-proposals"
            status, _, proposal = request(port, "POST", proposal_path, headers=auth)
            check(status == 201 and proposal["execution_available"] is False and proposal["status"] == "pending", "synthetic proposal failed")
            status, _, drafts_page = request(port, "GET", "/jarvis/synthetic/drafts?mail_id=" + mail_id, headers=auth)
            check(status == 200 and drafts_page["items"][0]["id"] == draft_id and "fictional reply" not in json.dumps(drafts_page), "draft queue failed or exposed body")
            status, _, pending_page = request(port, "GET", "/jarvis/synthetic/send-proposals", headers=auth)
            check(status == 200 and pending_page["items"][0]["id"] == proposal["id"] and not pending_page["execution_available"], "pending proposal queue failed")
            check(request(port, "GET", "/jarvis/synthetic/drafts", headers={**auth, "X-Jarvis-Assistant": "companion"})[0] == 403, "companion draft queue accepted")
            check(request(port, "PUT", "/jarvis/synthetic/drafts/" + draft_id, {**draft_payload, "body": "edited fictional reply"}, auth)[0] == 200, "draft update failed")
            approval_path = "/jarvis/synthetic/send-proposals/" + proposal["id"]
            status, _, invalid = request(port, "GET", approval_path, headers=auth)
            check(status == 200 and invalid["status"] == "invalidated" and invalid["payload"] == {}, "old proposal was not invalidated")
            check(not request(port, "GET", "/jarvis/synthetic/send-proposals", headers=auth)[2]["items"], "invalid proposal remained in queue")
            check(request(port, "POST", approval_path + "/approve", {"confirmed_hash": proposal["payload_hash"]}, auth)[0] == 409, "stale proposal approved")
            status, _, fresh = request(port, "POST", proposal_path, headers=auth)
            check(status == 201, "new proposal failed")
            fresh_path = "/jarvis/synthetic/send-proposals/" + fresh["id"]
            status, _, approved = request(port, "POST", fresh_path + "/approve", {"confirmed_hash": fresh["payload_hash"]}, auth)
            check(status == 200 and approved["status"] == "approved" and approved["execution_available"] is False, "synthetic approval failed")
            check(request(port, "POST", fresh_path + "/approve", {"confirmed_hash": fresh["payload_hash"]}, auth)[0] == 409, "proposal approved twice")
            check(request(port, "POST", fresh_path + "/execute", headers=auth)[0] == 404, "unexpected send executor exists")
            print("SYNTHETIC_MAIL_TO_TASK_DRAFT_APPROVAL_HTTP=PASS")
            model_path = "/jarvis/synthetic/model/requests"
            model_payload = {"purpose": "mail_summary", "source_id": mail_id}
            check(request(port, "POST", model_path, model_payload, {**auth, "X-Jarvis-Assistant": "companion"})[0] == 403, "companion used Jarvis fake model")
            status, _, fake = request(port, "POST", model_path, model_payload, auth)
            check(status == 201 and fake["simulated"] and fake["payload"]["destination"] == "local_process" and not fake["payload"]["leaves_device"], "fake model preview failed")
            fake_path = model_path + "/" + fake["id"]
            check(request(port, "POST", fake_path + "/execute", {"confirmed_hash": "0" * 64}, auth)[0] == 409, "wrong model preview hash accepted")
            check(request(port, "POST", fake_path + "/cancel", headers=auth)[0] == 200, "fake preview cancel failed")
            status, _, cancelled = request(port, "GET", fake_path, headers=auth)
            check(status == 200 and cancelled["status"] == "cancelled" and cancelled["payload"] is None, "cancelled fake payload visible")
            check(request(port, "POST", fake_path + "/execute", {"confirmed_hash": fake["payload_hash"]}, auth)[0] == 409, "cancelled fake request executed")
            status, _, reply = request(port, "POST", model_path, {"purpose": "mail_reply_suggestion", "source_id": mail_id}, auth)
            check(status == 201, "fake reply preview failed")
            reply_path = model_path + "/" + reply["id"]
            status, _, suggestion = request(port, "POST", reply_path + "/execute", {"confirmed_hash": reply["payload_hash"]}, auth)
            check(status == 200 and suggestion["simulated"] and suggestion["status"] == "proposed", "fake suggestion failed")
            check(request(port, "POST", reply_path + "/execute", {"confirmed_hash": reply["payload_hash"]}, auth)[0] == 409, "fake request executed twice")
            suggestion_path = "/jarvis/synthetic/model/suggestions/" + suggestion["suggestion_id"]
            status, _, suggestion_draft = request(port, "POST", suggestion_path + "/drafts", draft_payload, auth)
            check(status == 201 and suggestion_draft["synthetic"], "fake suggestion to draft failed")
            check(request(port, "POST", suggestion_path + "/drafts", draft_payload, auth)[0] == 409, "fake suggestion created duplicate drafts")
            check(request(port, "POST", suggestion_path + "/reject", headers=auth)[0] == 409, "accepted fake suggestion rejected")
            status, _, summary = request(port, "POST", model_path, model_payload, auth)
            check(status == 201, "fake summary preview failed")
            status, _, suggestion = request(port, "POST", model_path + "/" + summary["id"] + "/execute", {"confirmed_hash": summary["payload_hash"]}, auth)
            suggestion_path = "/jarvis/synthetic/model/suggestions/" + suggestion["suggestion_id"]
            check(status == 200 and request(port, "POST", suggestion_path + "/reject", headers=auth)[0] == 200, "fake suggestion rejection failed")
            status, _, rejected = request(port, "GET", suggestion_path, headers=auth)
            check(status == 200 and rejected["status"] == "rejected" and rejected["content"] == "", "rejected suggestion content remained visible")
            print("FAKE_MODEL_PREVIEW_CANCEL_SUGGESTION_DRAFT_HTTP=PASS")
            capture_payload = {"title": "Synthetic capture", "content": "fictional source body", "source_url": "https://example.invalid/item"}
            check(request(port, "POST", "/jarvis/synthetic/captures", {**capture_payload, "source_url": "https://example.com"}, auth)[0] == 422, "real source URL accepted")
            check(request(port, "POST", "/jarvis/synthetic/captures", capture_payload, {**auth, "X-Jarvis-Assistant": "companion"})[0] == 403, "companion created Jarvis capture")
            status, _, capture = request(port, "POST", "/jarvis/synthetic/captures", capture_payload, auth)
            check(status == 201 and capture["synthetic"], "synthetic capture failed")
            capture_id = capture["id"]
            note_payload = {"title": "Synthetic note", "body": "fictional note body", "source_capture_id": capture_id}
            status, _, note = request(port, "POST", "/jarvis/synthetic/notes", note_payload, auth)
            check(status == 201 and note["synthetic"], "synthetic note failed")
            note_id = note["id"]
            status, _, note_view = request(port, "GET", "/jarvis/synthetic/notes/" + note_id, headers=auth)
            check(status == 200 and "owner_id" not in note_view and note_view["source"]["available"], "note source or minimization failed")
            note_task_path = "/jarvis/synthetic/notes/" + note_id + "/tasks"
            note_auth = {**auth, "Idempotency-Key": "synthetic-note-task"}
            note_task = request(port, "POST", note_task_path, {"title": "Review fictional note"}, note_auth)
            note_retry = request(port, "POST", note_task_path, {"title": "Review fictional note"}, note_auth)
            check(note_task[0] == note_retry[0] == 201 and note_task[2]["id"] == note_retry[2]["id"], "note-to-task retry duplicated")
            check(request(port, "PUT", "/jarvis/synthetic/notes/" + note_id, {"title": "Updated synthetic note", "body": "updated fictional body"}, auth)[0] == 200, "note edit failed")
            check(request(port, "DELETE", "/jarvis/synthetic/captures/" + capture_id, headers=auth)[0] == 200, "capture delete failed")
            status, _, note_view = request(port, "GET", "/jarvis/synthetic/notes/" + note_id, headers=auth)
            check(status == 200 and not note_view["source"]["available"], "deleted capture still visible")
            check(request(port, "POST", "/jarvis/synthetic/captures/" + capture_id + "/restore", headers=auth)[0] == 200, "capture restore failed")
            check(request(port, "DELETE", "/jarvis/synthetic/notes/" + note_id, headers=auth)[0] == 200, "note delete failed")
            check(request(port, "GET", "/jarvis/synthetic/notes/" + note_id, headers=auth)[0] == 404, "deleted note visible")
            check(request(port, "POST", "/jarvis/synthetic/notes/" + note_id + "/restore", headers=auth)[0] == 200, "note restore failed")
            tag_path = "/jarvis/synthetic/knowledge-tags/note/" + note_id
            check(request(port, "POST", tag_path, {"tag": "Research"}, {**auth, "X-Jarvis-Assistant": "companion"})[0] == 403, "companion tag accepted")
            status, _, added = request(port, "POST", tag_path, {"tag": "Research"}, auth)
            check(status == 200 and added["added"], "knowledge tag add failed")
            status, _, duplicate = request(port, "POST", tag_path, {"tag": "RESEARCH"}, auth)
            check(status == 200 and not duplicate["added"] and duplicate["tag"] == "Research", "knowledge tag retry duplicated")
            status, _, tag_list = request(port, "GET", tag_path, headers=auth)
            check(status == 200 and tag_list["items"] == ["Research"], "knowledge tag list failed")
            status, _, tag_results = request(port, "GET", "/jarvis/synthetic/knowledge-tags/search?tag=research", headers=auth)
            check(status == 200 and len(tag_results["items"]) == 1 and tag_results["items"][0]["id"] == note_id and "fictional note body" not in json.dumps(tag_results), "knowledge tag search failed")
            check(request(port, "DELETE", "/jarvis/synthetic/notes/" + note_id, headers=auth)[0] == 200, "tagged note delete failed")
            check(not request(port, "GET", "/jarvis/synthetic/knowledge-tags/search?tag=research", headers=auth)[2]["items"], "deleted tagged note exposed")
            check(request(port, "POST", "/jarvis/synthetic/notes/" + note_id + "/restore", headers=auth)[0] == 200, "tagged note restore failed")
            check(request(port, "GET", tag_path, headers=auth)[2]["items"] == ["Research"], "restored note tags missing")
            status, _, note_page = request(port, "GET", "/jarvis/knowledge/note?tag=research&query=fictional", headers=auth)
            check(status == 200 and any(row["id"] == note_id for row in note_page["items"]) and "body" not in note_page["items"][0], "filtered knowledge listing failed")
            status, _, capture_page = request(port, "GET", "/jarvis/knowledge/capture?status=processed", headers=auth)
            check(status == 200 and any(row["id"] == capture_id for row in capture_page["items"]), "capture status listing failed")
            check(request(port, "GET", "/jarvis/knowledge/note", headers={**auth, "X-Jarvis-Assistant": "companion"})[0] == 403, "companion knowledge listing accepted")
            check(request(port, "GET", "/jarvis/knowledge/note?status=inbox", headers=auth)[0] == 422, "invalid note status accepted")
            check(request(port, "POST", tag_path + "/remove", {"tag": "research"}, auth)[2]["removed"], "knowledge tag removal failed")
            print("SYNTHETIC_KNOWLEDGE_LIST_HTTP=PASS")
            print("SYNTHETIC_KNOWLEDGE_TAG_HTTP=PASS")
            print("SYNTHETIC_CAPTURE_NOTE_TASK_RESTORE_HTTP=PASS")
            status, _, todo = request(port, "GET", "/todos/" + todo_id, headers=auth)
            check(status == 200 and todo["title"] == "Synthetic task" and "owner_id" not in todo, "todo fetch or owner minimization failed")
            check(request(port, "GET", "/todos/" + todo_id, headers={**auth, "X-Jarvis-Assistant": "companion"})[0] == 404, "assistant isolation failed")
            check(request(port, "PATCH", "/todos/" + todo_id, {"priority": "high"}, auth)[0] == 200, "todo edit failed")
            check(request(port, "POST", "/todos/" + todo_id + "/status", {"status": "done"}, auth)[0] == 200, "todo completion failed")
            check(request(port, "DELETE", "/todos/" + todo_id, headers=auth)[0] == 200, "todo delete failed")
            check(request(port, "GET", "/todos/" + todo_id, headers=auth)[0] == 404, "deleted todo visible")
            check(request(port, "POST", "/todos/" + todo_id + "/restore", headers=auth)[0] == 200, "todo restore failed")
            check(request(port, "GET", "/todos/" + todo_id, headers=auth)[0] == 200, "restored todo missing")
            status, _, reminder_task = request(port, "POST", "/todos", {"title": "Fictional reminder task", "due_at": "2020-01-01T00:00:00Z"}, auth)
            check(status == 201, "reminder task creation failed")
            rule_path = "/jarvis/synthetic/reminders/rules"
            payload = {"todo_id": reminder_task["id"], "offset_minutes": 0}
            check(request(port, "POST", rule_path, {**payload, "offset_minutes": True}, auth)[0] == 422, "boolean reminder offset accepted")
            check(request(port, "POST", rule_path, payload, {**auth, "X-Jarvis-Assistant": "companion"})[0] == 403, "companion reminder rule accepted")
            status, _, rule = request(port, "POST", rule_path, payload, auth)
            check(status == 201, "reminder rule creation failed")
            rule_id = rule["id"]
            check(request(port, "GET", rule_path, headers=auth)[2]["items"][0]["id"] == rule_id, "reminder rule listing failed")
            status, _, due = request(port, "GET", "/jarvis/synthetic/reminders/due", headers=auth)
            check(status == 200 and due["items"][0]["rule_id"] == rule_id, "due reminder missing")
            reminder_token = due["items"][0]["token"]
            ack_path = "/jarvis/synthetic/reminders/" + rule_id + "/acknowledge"
            status, _, ack = request(port, "POST", ack_path, {"token": reminder_token}, auth)
            check(status == 200 and not ack["already_acknowledged"], "reminder acknowledgement failed")
            status, _, ack = request(port, "POST", ack_path, {"token": reminder_token}, auth)
            check(status == 200 and ack["already_acknowledged"], "reminder acknowledgement retry failed")
            check(not request(port, "GET", "/jarvis/synthetic/reminders/due", headers=auth)[2]["items"], "acknowledged reminder appeared")
            check(request(port, "PATCH", "/todos/" + reminder_task["id"], {"due_at": "2020-01-02T00:00:00Z"}, auth)[0] == 200, "reminder task edit failed")
            check(request(port, "POST", ack_path, {"token": reminder_token}, auth)[0] == 409, "stale reminder token accepted")
            toggle_path = rule_path + "/" + rule_id + "/enabled"
            check(request(port, "PATCH", toggle_path, {"enabled": False}, auth)[0] == 200, "disable reminder failed")
            check(not request(port, "GET", "/jarvis/synthetic/reminders/due", headers=auth)[2]["items"], "disabled reminder appeared")
            check(request(port, "PATCH", toggle_path, {"enabled": True}, auth)[0] == 200, "enable reminder failed")
            check(len(request(port, "GET", "/jarvis/synthetic/reminders/due", headers=auth)[2]["items"]) == 1, "enabled reminder missing")
            print("SYNTHETIC_REMINDER_LIFECYCLE_HTTP=PASS")
            rule_messages = []
            for suffix in ("one", "two"):
                status, _, created = request(port, "POST", "/jarvis/synthetic/mail", {"sender": "rules@example.invalid", "subject": "Rule " + suffix, "body": "fictional body"}, auth)
                check(status == 201, "rule test mail creation failed")
                rule_messages.append(created["id"])
            mail_rule_path = "/jarvis/synthetic/mail-rules"
            mail_rule_payload = {"name": "Rule test", "sender_exact": "rules@example.invalid", "target_triage": "needs_reply", "target_priority": "high"}
            check(request(port, "POST", mail_rule_path, mail_rule_payload, {**auth, "X-Jarvis-Assistant": "companion"})[0] == 403, "companion rule creation accepted")
            status, _, created = request(port, "POST", mail_rule_path, mail_rule_payload, auth)
            check(status == 201, "mail rule creation failed")
            mail_rule_id = created["id"]
            enabled_path = mail_rule_path + "/" + mail_rule_id + "/enabled"
            check(request(port, "PATCH", enabled_path, {"enabled": True}, auth)[0] == 200, "mail rule enable failed")
            status, _, rule_preview = request(port, "POST", mail_rule_path + "/" + mail_rule_id + "/previews", headers=auth)
            check(status == 201 and len(rule_preview["plan"]["items"]) == 2 and "fictional body" not in json.dumps(rule_preview), "mail rule preview failed or leaked body")
            preview_path = "/jarvis/synthetic/mail-rule-previews/" + rule_preview["id"]
            check(request(port, "PATCH", enabled_path, {"enabled": True}, auth)[0] == 200, "mail rule enable retry failed")
            status, _, loaded_preview = request(port, "GET", preview_path, headers=auth)
            check(status == 200 and loaded_preview["status"] == "pending" and loaded_preview["plan_hash"] == rule_preview["plan_hash"], "pending rule preview did not survive retry")
            check(request(port, "POST", preview_path + "/apply", {"confirmed_hash": "0" * 64}, auth)[0] == 409, "wrong rule approval accepted")
            status, _, applied = request(port, "POST", preview_path + "/apply", {"confirmed_hash": rule_preview["plan_hash"]}, auth)
            check(status == 200 and applied["changed"] == 2, "mail rule application failed")
            status, _, finished = request(port, "GET", preview_path, headers=auth)
            check(status == 200 and finished["status"] == "applied" and "plan" not in finished, "closed preview still exposed plan")
            check(request(port, "POST", preview_path + "/apply", {"confirmed_hash": rule_preview["plan_hash"]}, auth)[0] == 409, "mail rule applied twice")
            check(request(port, "PATCH", "/jarvis/synthetic/mail/" + rule_messages[0] + "/triage", {"triage_status": "waiting", "priority": "normal"}, auth)[0] == 200, "mail edit failed")
            status, _, undone = request(port, "POST", preview_path + "/undo", headers=auth)
            check(status == 200 and undone["restored"] == 1 and undone["conflicts"] == 1, "mail rule undo conflict failed")
            status, _, rules_view = request(port, "GET", mail_rule_path, headers=auth)
            check(status == 200 and any(item["id"] == mail_rule_id for item in rules_view["items"]), "mail rule listing failed")
            status, _, inbox = request(port, "GET", "/jarvis/mail?query=Rule&limit=1", headers=auth)
            check(status == 200 and len(inbox["items"]) == 1 and inbox["next_cursor"] and "body" not in inbox["items"][0], "mail listing page failed")
            status, _, next_inbox = request(port, "GET", "/jarvis/mail?query=Rule&limit=1&cursor=" + inbox["next_cursor"], headers=auth)
            check(status == 200 and len(next_inbox["items"]) == 1 and inbox["items"][0]["id"] != next_inbox["items"][0]["id"], "mail listing next page failed")
            check(request(port, "GET", "/jarvis/mail?query=different&cursor=" + inbox["next_cursor"], headers=auth)[0] == 422, "mail cursor used with different filters")
            check(request(port, "GET", "/jarvis/mail", headers={**auth, "X-Jarvis-Assistant": "companion"})[0] == 403, "companion mailbox listing accepted")
            print("SYNTHETIC_MAIL_LIST_HTTP=PASS")
            print("SYNTHETIC_MAIL_RULE_PREVIEW_APPLY_UNDO_HTTP=PASS")
            print("HTTP_LOOPBACK_AUTH_GUARD_TODO_JARVIS_SEARCH_AND_ISOLATION=PASS")
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

print("HTTP_VERIFICATION=PASS")
