"""J7 synthetic export scoping and validation checks; no file export or restore."""
import copy
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.database import apply_migrations, connect
from app.security.assistant_context import AssistantContext
from app.services.conversation_service import ConversationService
from app.services.jarvis_mail_todo_workflow import JarvisMailTodoWorkflow
from app.services.synthetic_export_service import SyntheticExportService
from app.services.synthetic_knowledge_service import SyntheticKnowledgeService
from app.services.synthetic_mail_service import SyntheticMailService
from app.services.synthetic_model_gateway import SyntheticModelGateway
from app.services.todo_service import TodoService
from app.services.synthetic_reminder_service import SyntheticReminderService
from app.services.synthetic_knowledge_tag_service import SyntheticKnowledgeTagService


def expect_value_error(operation, *args):
    try:
        operation(*args)
    except ValueError:
        return
    raise AssertionError("Expected ValueError")


def rehash(bundle):
    body = {key: bundle[key] for key in ("format", "version", "created_at", "sections", "manifest")}
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    bundle["content_sha256"] = hashlib.sha256(raw).hexdigest()


with tempfile.TemporaryDirectory(prefix="jarvis-j7-check-") as directory:
    os.environ["JARVIS_DATA_MODE"] = "synthetic"
    os.environ["JARVIS_DATA_DIR"] = directory
    apply_migrations()
    owner, other = AssistantContext("owner-a", "jarvis"), AssistantContext("owner-b", "jarvis")
    mail, knowledge, todos = SyntheticMailService(), SyntheticKnowledgeService(), TodoService()
    workflow, model, exporter = JarvisMailTodoWorkflow(todos), SyntheticModelGateway(), SyntheticExportService()
    item = mail.create_message(owner, "sender@example.invalid", "synthetic subject", "owner mail content")
    mail.create_message(other, "foreign@example.invalid", "foreign secret subject", "foreign private body")
    draft = mail.create_draft(owner, item, "me@example.invalid", ["sender@example.invalid"], "Re", "synthetic draft body")
    mail.prepare_send(owner, draft)
    model.prepare(owner, "mail_summary", item)
    capture = knowledge.create_capture(owner, "Captured", "synthetic capture", "https://example.invalid/a", confirmed_by_user=True)
    note = knowledge.create_note(owner, "Note", "synthetic note", capture, confirmed_by_user=True)
    deleted_source = knowledge.create_capture(owner, "Deleted source", "not exported", confirmed_by_user=True)
    knowledge.create_note(owner, "Orphan note", "still visible", deleted_source, confirmed_by_user=True)
    knowledge.delete_capture(owner, deleted_source)
    task = workflow.create_from_mail(owner, item, "Reply", confirmed_by_user=True)
    todos.update(owner, task, due_at="2027-01-01T12:00:00Z")
    reminder_id = SyntheticReminderService().create_rule(owner, task, 60, confirmed_by_user=True)
    SyntheticKnowledgeTagService().add(owner, "note", note, "fictional-export-tag", confirmed_by_user=True)
    conversation = ConversationService().create(owner, "not exported conversation")
    msg = ConversationService().append_message(owner, conversation, "user", "not exported chat content")
    todos.create(owner, "Task with omitted source", sources=[{"type":"message", "id":msg}], confirmed_by_user=True)
    deleted_task = todos.create(owner, "deleted task", confirmed_by_user=True)
    todos.delete(owner, deleted_task)

    bundle = exporter.build(owner)
    preview = exporter.preview(owner, bundle)
    assert preview["encrypted"] is False and preview["restore_available"] is False
    assert preview["counts"] == {"emails":1,"drafts":1,"captures":1,"notes":2,"todos":2,"todo_sources":1}
    assert preview["omitted_source_links"] == 1 and preview["unavailable_note_sources"] == 1
    assert any(row["source_capture_id"] is None for row in bundle["sections"]["notes"])
    serialized = json.dumps(bundle, ensure_ascii=False)
    assert reminder_id not in serialized and "fictional-export-tag" not in serialized
    for hidden in ("foreign secret subject", "foreign private body", "not exported chat content", "not exported conversation", "deleted task", "not exported"):
        assert hidden not in serialized, hidden
    for excluded in ("approvals", "model_requests", "credentials", "audit", "companion"):
        assert excluded in preview["excluded"] and excluded not in bundle["sections"]
    assert exporter.preview(other, exporter.build(other))["counts"]["emails"] == 1
    print("OWNER_SCOPE_SECTIONS_AND_OMISSIONS=PASS")

    tampered = copy.deepcopy(bundle)
    tampered["sections"]["emails"][0]["body"] = "changed"
    expect_value_error(exporter.preview, owner, tampered)
    malformed = copy.deepcopy(bundle)
    malformed["sections"]["notes"][0]["source_capture_id"] = str(uuid4())
    rehash(malformed)
    expect_value_error(exporter.preview, owner, malformed)
    bad_address = copy.deepcopy(bundle)
    bad_address["sections"]["emails"][0]["sender"] = "real@example.com"
    rehash(bad_address)
    expect_value_error(exporter.preview, owner, bad_address)
    extra = copy.deepcopy(bundle)
    extra["sections"]["todos"][0]["owner_id"] = "foreign"
    rehash(extra)
    expect_value_error(exporter.preview, owner, extra)
    assert exporter.preview(owner, bundle)["restore_available"] is False
    oversized = copy.deepcopy(bundle)
    oversized["sections"]["emails"][0]["body"] = "x" * 5_000_001
    expect_value_error(exporter.preview, owner, oversized)
    deep = copy.deepcopy(bundle)
    value = []
    for _ in range(20):
        value = [value]
    deep["sections"]["emails"][0]["body"] = value
    expect_value_error(exporter.preview, owner, deep)
    cyclic = copy.deepcopy(bundle)
    loop = []
    loop.append(loop)
    cyclic["sections"]["emails"][0]["body"] = loop
    expect_value_error(exporter.preview, owner, cyclic)
    invalid_unicode = copy.deepcopy(bundle)
    invalid_unicode["sections"]["emails"][0]["body"] = "\ud800"
    expect_value_error(exporter.preview, owner, invalid_unicode)
    assert "synthetic_connections" in preview["excluded"] and "sync_runs" in preview["excluded"]
    older = copy.deepcopy(bundle)
    older["version"] = 1
    older["manifest"]["excluded"] = older["manifest"]["excluded"][:-9]
    rehash(older)
    assert exporter.preview(owner, older)["version"] == 1
    previous = copy.deepcopy(bundle)
    previous["version"] = 2
    previous["manifest"]["excluded"] = previous["manifest"]["excluded"][:-7]
    rehash(previous)
    assert exporter.preview(owner, previous)["version"] == 2
    assert "synthetic_rules" in preview["excluded"] and "rule_changes" in preview["excluded"]
    third = copy.deepcopy(bundle)
    third["version"] = 3
    third["manifest"]["excluded"] = third["manifest"]["excluded"][:-4]
    rehash(third)
    assert exporter.preview(owner, third)["version"] == 3
    assert "todo_idempotency" in preview["excluded"]
    fourth = copy.deepcopy(bundle)
    fourth["version"] = 4
    fourth["manifest"]["excluded"] = fourth["manifest"]["excluded"][:-3]
    rehash(fourth)
    assert exporter.preview(owner, fourth)["version"] == 4
    assert "reminder_rules" in preview["excluded"] and "reminder_acknowledgements" in preview["excluded"]
    fifth = copy.deepcopy(bundle)
    fifth["version"] = 5
    fifth["manifest"]["excluded"] = fifth["manifest"]["excluded"][:-1]
    rehash(fifth)
    assert exporter.preview(owner, fifth)["version"] == 5
    assert "knowledge_tags" in preview["excluded"]
    print("TAMPER_SCHEMA_REFERENCE_ADDRESS_AND_BOUNDED_INPUT=PASS")

    with connect() as conn:
        audit = " ".join(row[0] for row in conn.execute("SELECT metadata_json FROM audit_events"))
    assert "owner mail content" not in audit and "synthetic note" not in audit
    print("EXPORT_AUDIT_METADATA_ONLY=PASS")

print("J7_SYNTHETIC_EXPORT_VERIFICATION=PASS")
