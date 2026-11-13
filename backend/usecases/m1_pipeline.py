"""M1 vertical loop: import -> suggestion -> decision -> todo/draft -> audit.

All rows are fictional. The loop never sends anything and never touches a real
account; the only outbound-capable place in the product remains absent on purpose.
"""
import hashlib
import json
import uuid
from datetime import datetime, timezone

from . import m1_rules

OWNER = "local"
ASSISTANT = "jarvis"


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _id(prefix):
    return "%s-%s" % (prefix, uuid.uuid4().hex[:12])


def load_dataset(path):
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def audit(connection, event_type, object_type, object_id, *, actor_type="user", actor_id=OWNER, metadata=None):
    connection.execute(
        "INSERT INTO audit_events (id, owner_id, actor_type, actor_id, event_type, object_type, object_id,"
        " metadata_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (_id("audit"), OWNER, actor_type, actor_id, event_type, object_type, object_id,
         json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True), _now()),
    )


def import_artifact(connection, row):
    """Insert one artifact; identical text is recorded as a duplicate."""
    content = row["text"]
    dedupe_key = hashlib.sha256(content.encode("utf-8")).hexdigest()
    existing = connection.execute(
        "SELECT id FROM m1_artifacts WHERE dedupe_key=? ORDER BY imported_at LIMIT 1", (dedupe_key,)
    ).fetchone()
    artifact_id = _id("art")
    duplicate_of = existing["id"] if existing else None
    connection.execute(
        "INSERT INTO m1_artifacts (id, owner_id, kind, received_at, thread_len, dedupe_key, duplicate_of,"
        " content, labels_json, imported_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (artifact_id, OWNER, row["kind"], row["received_at"], int(row.get("thread_len", 1)), dedupe_key,
         duplicate_of, content, json.dumps(row.get("labels", {}), ensure_ascii=False), _now()),
    )
    audit(connection, "artifact_imported", "m1_artifact", artifact_id,
          actor_type="system", actor_id="importer",
          metadata={"kind": row["kind"], "duplicate_of": duplicate_of, "thread_len": row.get("thread_len", 1)})
    return {"artifact_id": artifact_id, "duplicate_of": duplicate_of}


def suggest(connection, artifact_id, engine=None):
    row = connection.execute("SELECT * FROM m1_artifacts WHERE id=?", (artifact_id,)).fetchone()
    if row is None:
        raise KeyError("unknown artifact: " + artifact_id)
    engine = engine or m1_rules.suggest
    result = engine(row["content"], kind=row["kind"])
    if result.classification not in m1_rules.CLASSES or result.priority not in m1_rules.PRIORITIES:
        raise ValueError("candidate has an invalid classification or priority")
    if any(not isinstance(item, dict) or not isinstance(item.get("span"), str)
           or item["span"] not in row["content"] for item in result.evidence):
        raise ValueError("candidate evidence is not a verbatim source span")
    if (result.wants_todo or result.wants_draft) and not result.evidence:
        raise ValueError("actionable candidate requires source evidence")
    rule_veto = False
    if engine is not m1_rules.suggest:
        rule_screen = m1_rules.suggest(row["content"], kind=row["kind"])
        if rule_screen.classification == "injection":
            result = rule_screen
            rule_veto = True
    action_veto = False
    if result.classification == "injection" and (result.wants_todo or result.wants_draft):
        result = m1_rules.Suggestion("injection", "high", False, False, result.confidence, result.evidence)
        action_veto = True
    suggestion_id = _id("sug")
    engine_name = getattr(engine, "name", m1_rules.ENGINE_NAME) + ("+rules-veto" if rule_veto else "")
    connection.execute(
        "INSERT INTO m1_suggestions (id, artifact_id, engine, classification, priority, wants_todo, wants_draft,"
        " confidence, evidence_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (suggestion_id, artifact_id, engine_name, result.classification, result.priority,
         int(result.wants_todo), int(result.wants_draft), float(result.confidence),
         json.dumps(result.evidence, ensure_ascii=False), _now()),
    )
    supported = all(item["span"] in row["content"] for item in result.evidence) if result.evidence else False
    audit(connection, "suggestion_created", "m1_suggestion", suggestion_id, actor_type="assistant", actor_id=ASSISTANT,
          metadata={"classification": result.classification, "priority": result.priority,
                    "evidence_supported": supported, "duplicate": bool(row["duplicate_of"]),
                    "rule_veto": rule_veto, "action_veto": action_veto})
    return {
        "suggestion_id": suggestion_id, "artifact_id": artifact_id, "engine": engine_name,
        "classification": result.classification, "priority": result.priority,
        "todo": result.wants_todo, "draft": result.wants_draft,
        "confidence": result.confidence, "evidence": result.evidence,
        "evidence_supported": supported, "duplicate_of": row["duplicate_of"],
        "rule_veto": rule_veto, "action_veto": action_veto,
    }


def decide(connection, suggestion_id, action, edits=None):
    if action not in ("accept", "modify", "reject"):
        raise ValueError("action must be accept, modify or reject")
    if connection.execute("SELECT 1 FROM m1_suggestions WHERE id=?", (suggestion_id,)).fetchone() is None:
        raise KeyError("unknown suggestion: " + suggestion_id)
    decision_id = _id("dec")
    connection.execute(
        "INSERT INTO m1_decisions (id, suggestion_id, action, edits_json, created_at) VALUES (?,?,?,?,?)",
        (decision_id, suggestion_id, action, json.dumps(edits or {}, ensure_ascii=False), _now()),
    )
    audit(connection, "decision_recorded", "m1_decision", decision_id,
          metadata={"action": action, "edits": sorted((edits or {}).keys())})
    return {"decision_id": decision_id, "action": action}


def materialize(connection, decision_id):
    """Create the todo or draft implied by the accepted/edited suggestion."""
    row = connection.execute(
        "SELECT d.id AS decision_id, d.action, d.edits_json, s.id AS suggestion_id, s.artifact_id,"
        " s.wants_todo, s.wants_draft, s.priority, s.evidence_json, s.classification, a.content, a.kind"
        " FROM m1_decisions d JOIN m1_suggestions s ON s.id = d.suggestion_id"
        " JOIN m1_artifacts a ON a.id = s.artifact_id WHERE d.id=?",
        (decision_id,),
    ).fetchone()
    if row is None:
        raise KeyError("unknown decision: " + decision_id)
    if row["action"] == "reject":
        return {"todo_id": None, "draft_id": None, "skipped": True}
    edits = json.loads(row["edits_json"] or "{}")
    wants_todo = bool(edits.get("todo", row["wants_todo"]))
    wants_draft = bool(edits.get("draft", row["wants_draft"]))
    if row["classification"] == "injection" and (wants_todo or wants_draft):
        raise ValueError("an injection-classified artifact cannot create work")
    title = edits.get("title") or _title_from(row["content"], row["kind"])
    evidence = json.loads(row["evidence_json"] or "[]")

    todo_id = None
    if wants_todo:
        todo_id = _id("todo")
        connection.execute(
            "INSERT INTO todos (id, owner_id, created_by_assistant, title, description, status, priority,"
            " due_at, completed_at, created_at, updated_at, deleted_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (todo_id, OWNER, ASSISTANT, title,
             "来源：m1_artifact %s\n依据：%s" % (row["artifact_id"], ", ".join(item["span"] for item in evidence) or "证据不足"),
             "open", row["priority"], None, None, _now(), _now(), None),
        )
        audit(connection, "todo_created", "todo", todo_id,
              metadata={"artifact_id": row["artifact_id"], "priority": row["priority"]})

    draft_id = None
    if wants_draft:
        draft_id = _id("draft")
        body = edits.get("body") or (
            "草稿（未发送）：\n感谢来信，我需要先核对以下内容再回复：\n- %s\n\n依据：%s"
            % (title, ", ".join(item["span"] for item in evidence) or "证据不足")
        )
        connection.execute(
            "INSERT INTO m1_drafts (id, owner_id, artifact_id, subject, body, created_at) VALUES (?,?,?,?,?,?)",
            (draft_id, OWNER, row["artifact_id"], edits.get("subject") or "回复：" + title, body, _now()),
        )
        audit(connection, "draft_created", "m1_draft", draft_id,
              metadata={"artifact_id": row["artifact_id"], "sent": False})

    return {"todo_id": todo_id, "draft_id": draft_id, "title": title}


def _title_from(content, kind):
    cleaned = " ".join((content or "").split())
    return (kind + " | " + cleaned)[:80]


def run_item(connection, row, *, action="accept", edits=None):
    """Run one artifact through the whole loop and return the transcript."""
    imported = import_artifact(connection, row)
    suggestion = suggest(connection, imported["artifact_id"])
    decision = decide(connection, suggestion["suggestion_id"], action, edits)
    created = materialize(connection, decision["decision_id"])
    events = connection.execute(
        "SELECT event_type, object_type, object_id FROM audit_events WHERE object_id IN (?,?,?,?,?) ORDER BY created_at",
        (imported["artifact_id"], suggestion["suggestion_id"], decision["decision_id"],
         created["todo_id"] or "-", created["draft_id"] or "-"),
    ).fetchall()
    return {
        "artifact": imported, "suggestion": suggestion, "decision": decision, "created": created,
        "audit": [dict(event) for event in events],
    }
