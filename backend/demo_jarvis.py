"""Isolated, dependency-free Jarvis demonstration using fictional records only.

No external accounts or network calls; the temporary database is removed on exit.
Approvals are simulated for the demonstration, not approvals for real actions.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

# Embedded Python may omit the script directory from sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.database import apply_migrations
from app.security.assistant_context import AssistantContext
from app.services.jarvis_knowledge_todo_workflow import JarvisKnowledgeTodoWorkflow
from app.services.jarvis_mail_todo_workflow import JarvisMailTodoWorkflow
from app.services.jarvis_overview_service import JarvisOverviewService
from app.services.jarvis_task_query_service import JarvisTaskQueryService
from app.services.synthetic_knowledge_service import SyntheticKnowledgeService
from app.services.synthetic_mail_service import SyntheticMailService
from app.services.synthetic_model_gateway import SyntheticModelGateway


def run_demo():
    with tempfile.TemporaryDirectory(prefix="jarvis-demo-") as directory:
        os.environ["JARVIS_DATA_MODE"] = "synthetic"
        os.environ["JARVIS_DATA_DIR"] = directory
        apply_migrations()
        ctx = AssistantContext("demo-owner", "jarvis")
        mail = SyntheticMailService()
        knowledge = SyntheticKnowledgeService()
        model = SyntheticModelGateway()
        item = mail.create_message(ctx, "colleague@example.invalid", "Project meeting", "Please review the fictional project plan.")
        mail.triage(ctx, item, "needs_reply", "high")
        task = JarvisMailTodoWorkflow().create_from_mail(ctx, item, "Review project plan", priority="high", confirmed_by_user=True)
        capture = knowledge.create_capture(ctx, "Fictional note", "A sample idea to organize.", "https://example.invalid/sample", confirmed_by_user=True)
        note = knowledge.create_note(ctx, "Project idea", "A sample idea to organize.", capture, confirmed_by_user=True)
        JarvisKnowledgeTodoWorkflow().create_from_source(ctx, "note", note, "Organize idea", confirmed_by_user=True)
        preview = model.prepare(ctx, "mail_summary", item)
        result = model.execute(ctx, preview["id"], preview["payload_hash"], confirmed_by_user=True)
        draft = mail.create_draft(ctx, item, "me@example.invalid", ["colleague@example.invalid"], "Re: Project meeting", "This is a fictional local draft.")
        proposal = mail.prepare_send(ctx, draft)
        overview = JarvisOverviewService().snapshot(ctx)
        tasks = JarvisTaskQueryService().search(ctx)
        return {
            "synthetic_only": True,
            "temporary_database": True,
            "external_connections": False,
            "actual_send_available": False,
            "approvals_are_simulated": True,
            "mail_needs_reply": overview["mail"]["needs_reply"],
            "open_todos": overview["todos"]["open"],
            "knowledge_notes": overview["knowledge"]["total"],
            "pending_send_proposals": overview["send_approvals"]["pending"],
            "task_titles": [entry["title"] for entry in tasks["items"]],
            "mail_source_available": JarvisMailTodoWorkflow().get_mail_sources(ctx, task)[0]["available"],
            "local_fake_model": result["simulated"],
            "proposal_status": proposal["status"],
        }


if __name__ == "__main__":
    if sys.argv[1:] not in ([], ["--json"]):
        raise SystemExit("Usage: python backend/demo_jarvis.py [--json]")
    data = run_demo()
    if sys.argv[1:] == ["--json"]:
        print(json.dumps(data, ensure_ascii=True, sort_keys=True))
    else:
        print("JARVIS LOCAL DEMO - fictional data only")
        print("Temporary database is deleted at exit; no accounts or network are used.")
        print("Approvals below are simulated; no actual email can be sent.")
        print("Mail needing reply:", data["mail_needs_reply"])
        print("Open tasks:", data["open_todos"], "/", ", ".join(data["task_titles"]))
        print("Knowledge notes:", data["knowledge_notes"])
        print("Local fake model used:", data["local_fake_model"])
        print("Pending send proposals:", data["pending_send_proposals"])
        print("Source trace available:", data["mail_source_available"])
