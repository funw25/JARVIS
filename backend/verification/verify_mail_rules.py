"""J9 local synthetic-mail rule preview, atomic apply and conflict-safe undo."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.database import apply_migrations, connect
from app.security.assistant_context import AccessDenied, AssistantContext, InvalidTransition, NotFound
from app.services.synthetic_mail_rule_service import SyntheticMailRuleService
from app.services.synthetic_mail_service import SyntheticMailService


def expect_error(kind, operation, *args, **kwargs):
    try:
        operation(*args, **kwargs)
    except kind:
        return
    raise AssertionError(f"Expected {kind.__name__}")


with tempfile.TemporaryDirectory(prefix="jarvis-j9-check-") as directory:
    os.environ["JARVIS_DATA_MODE"] = "synthetic"
    os.environ["JARVIS_DATA_DIR"] = directory
    apply_migrations()
    owner, other = AssistantContext("owner-a", "jarvis"), AssistantContext("owner-b", "jarvis")
    mail, rules = SyntheticMailService(), SyntheticMailRuleService()
    one = mail.create_message(owner, "team@example.invalid", "项目会议 one", "private synthetic body one")
    two = mail.create_message(owner, "team@example.invalid", "项目会议 two", "private synthetic body two")
    ignored = mail.create_message(owner, "other@example.invalid", "项目会议", "other body")
    foreign = mail.create_message(other, "team@example.invalid", "项目会议 foreign", "foreign body")
    expect_error(AccessDenied, rules.create, owner, "rule", sender_exact="team@example.invalid", target_priority="high")
    expect_error(ValueError, rules.create, owner, "rule", target_priority="high", confirmed_by_user=True)
    rule = rules.create(owner, "mark meetings", sender_exact="team@example.invalid", subject_contains="项目会议", target_triage="needs_reply", target_priority="high", confirmed_by_user=True)
    expect_error(NotFound, rules.set_enabled, other, rule, True, confirmed_by_user=True)
    disabled = rules.preview(owner, rule)
    assert disabled["rule_enabled"] is False and len(disabled["plan"]["items"]) == 2
    expect_error(InvalidTransition, rules.apply, owner, disabled["id"], disabled["plan_hash"], confirmed_by_user=True)
    rules.set_enabled(owner, rule, True, confirmed_by_user=True)
    print("EXPLICIT_ENABLE_PREVIEW_AND_OWNER_SCOPE=PASS")

    stale = rules.preview(owner, rule)
    rules.set_enabled(owner, rule, True, confirmed_by_user=True)
    assert rules.get_preview(owner, stale["id"])["plan_hash"] == stale["plan_hash"]
    expect_error(NotFound, rules.get_preview, other, stale["id"])
    mail.triage(owner, one, "waiting", "normal")
    expect_error(InvalidTransition, rules.apply, owner, stale["id"], stale["plan_hash"], confirmed_by_user=True)
    assert mail.get_message(owner, two)["triage_status"] == "new"
    with connect() as conn:
        assert tuple(conn.execute("SELECT status,plan_json FROM synthetic_rule_previews WHERE id=?", (stale["id"],)).fetchone()) == ("invalidated", "{}")
    plan = rules.preview(owner, rule)
    expect_error(AccessDenied, rules.apply, owner, plan["id"], plan["plan_hash"])
    expect_error(InvalidTransition, rules.apply, owner, plan["id"], "wrong", confirmed_by_user=True)
    assert rules.apply(owner, plan["id"], plan["plan_hash"], confirmed_by_user=True) == 2
    with connect() as conn:
        assert conn.execute("SELECT plan_json FROM synthetic_rule_previews WHERE id=?", (plan["id"],)).fetchone()[0] == "{}"
    assert mail.get_message(owner, one)["triage_status"] == "needs_reply"
    assert mail.get_message(owner, ignored)["triage_status"] == "new"
    assert mail.get_message(other, foreign)["triage_status"] == "new"
    expect_error(InvalidTransition, rules.apply, owner, plan["id"], plan["plan_hash"], confirmed_by_user=True)
    print("STALE_PREVIEW_ATOMIC_APPLY_AND_DUPLICATE_REJECTION=PASS")

    mail.triage(owner, one, "waiting", "normal")
    mail.triage(owner, one, "needs_reply", "high")
    undo = rules.undo(owner, plan["id"], confirmed_by_user=True)
    assert undo == {"restored": 1, "conflicts": 1}, undo
    assert mail.get_message(owner, one)["triage_status"] == "needs_reply"
    assert mail.get_message(owner, two)["triage_status"] == "new"
    assert rules.undo(owner, plan["id"], confirmed_by_user=True) == {"restored": 0, "conflicts": 0}
    print("VERSION_BASED_UNDO_CONFLICT_PROTECTION=PASS")

    expired = rules.preview(owner, rule)
    with connect() as conn:
        conn.execute("UPDATE synthetic_rule_previews SET expires_at='2000-01-01T00:00:00.000Z' WHERE id=?", (expired["id"],))
    assert rules.get_preview(owner, expired["id"])["status"] == "expired"
    assert "plan" not in rules.get_preview(owner, expired["id"])
    with connect() as conn:
        assert conn.execute("SELECT plan_json FROM synthetic_rule_previews WHERE id=?", (expired["id"],)).fetchone()[0] == "{}"
    expect_error(InvalidTransition, rules.apply, owner, expired["id"], expired["plan_hash"], confirmed_by_user=True)
    pending = rules.preview(owner, rule)
    rules.set_enabled(owner, rule, False, confirmed_by_user=True)
    expect_error(InvalidTransition, rules.apply, owner, pending["id"], pending["plan_hash"], confirmed_by_user=True)
    with connect() as conn:
        assert conn.execute("SELECT plan_json FROM synthetic_rule_previews WHERE id=?", (pending["id"],)).fetchone()[0] == "{}"
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM action_proposals").fetchone()[0] == 0
        audit = " ".join(row[0] for row in conn.execute("SELECT metadata_json FROM audit_events"))
    assert "private synthetic body" not in audit
    print("EXPIRY_DISABLE_NO_EXTERNAL_ACTION_AND_AUDIT=PASS")

print("J9_LOCAL_RULES_VERIFICATION=PASS")
