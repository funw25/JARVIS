"""Jarvis synthetic knowledge endpoints."""
from fastapi import APIRouter
from fastapi import Depends, status
from ...security.assistant_context import AssistantContext
from ...services.synthetic_knowledge_service import SyntheticKnowledgeService
from ...services.jarvis_knowledge_todo_workflow import JarvisKnowledgeTodoWorkflow
from .routes import context_for, invoke, single_idempotency_key
from .schemas import SyntheticSourceTodo, SyntheticCaptureCreate, SyntheticCaptureStatus, SyntheticNoteCreate, SyntheticNoteUpdate

router = APIRouter()
knowledge = SyntheticKnowledgeService()
knowledge_todos = JarvisKnowledgeTodoWorkflow()


@router.post("/synthetic/captures", status_code=status.HTTP_201_CREATED)
def create_synthetic_capture(payload: SyntheticCaptureCreate, ctx: AssistantContext = Depends(context_for)):
    return {"id": invoke(knowledge.create_capture, ctx, payload.title, payload.content, payload.source_url, confirmed_by_user=True), "synthetic": True}


@router.get("/synthetic/captures/{capture_id}")
def get_synthetic_capture(capture_id: str, ctx: AssistantContext = Depends(context_for)):
    result = invoke(knowledge.get_capture, ctx, capture_id)
    result.pop("owner_id", None)
    return result


@router.patch("/synthetic/captures/{capture_id}/status")
def change_synthetic_capture_status(capture_id: str, payload: SyntheticCaptureStatus, ctx: AssistantContext = Depends(context_for)):
    invoke(knowledge.set_capture_status, ctx, capture_id, payload.status)
    return {"status": payload.status, "synthetic": True}


@router.delete("/synthetic/captures/{capture_id}")
def delete_synthetic_capture(capture_id: str, ctx: AssistantContext = Depends(context_for)):
    invoke(knowledge.delete_capture, ctx, capture_id)
    return {"status": "deleted", "recoverable": True}


@router.post("/synthetic/captures/{capture_id}/restore")
def restore_synthetic_capture(capture_id: str, ctx: AssistantContext = Depends(context_for)):
    invoke(knowledge.restore_capture, ctx, capture_id)
    return {"status": "restored", "synthetic": True}


@router.post("/synthetic/notes", status_code=status.HTTP_201_CREATED)
def create_synthetic_note(payload: SyntheticNoteCreate, ctx: AssistantContext = Depends(context_for)):
    return {"id": invoke(knowledge.create_note, ctx, payload.title, payload.body, payload.source_capture_id, confirmed_by_user=True), "synthetic": True}


@router.get("/synthetic/notes/{note_id}")
def get_synthetic_note(note_id: str, ctx: AssistantContext = Depends(context_for)):
    result = invoke(knowledge.get_note, ctx, note_id)
    result.pop("owner_id", None)
    return result


@router.put("/synthetic/notes/{note_id}")
def edit_synthetic_note(note_id: str, payload: SyntheticNoteUpdate, ctx: AssistantContext = Depends(context_for)):
    invoke(knowledge.edit_note, ctx, note_id, payload.title, payload.body)
    return {"status": "updated", "synthetic": True}


@router.delete("/synthetic/notes/{note_id}")
def delete_synthetic_note(note_id: str, ctx: AssistantContext = Depends(context_for)):
    invoke(knowledge.delete_note, ctx, note_id)
    return {"status": "deleted", "recoverable": True}


@router.post("/synthetic/notes/{note_id}/restore")
def restore_synthetic_note(note_id: str, ctx: AssistantContext = Depends(context_for)):
    invoke(knowledge.restore_note, ctx, note_id)
    return {"status": "restored", "synthetic": True}


@router.post("/synthetic/captures/{capture_id}/tasks", status_code=status.HTTP_201_CREATED)
def capture_to_synthetic_task(capture_id: str, payload: SyntheticSourceTodo,
                              ctx: AssistantContext = Depends(context_for),
                              key: str | None = Depends(single_idempotency_key)):
    return {"id": invoke(knowledge_todos.create_from_source, ctx, "capture", capture_id, payload.title,
                         payload.description, payload.priority, payload.due_at, confirmed_by_user=True, idempotency_key=key)}


@router.post("/synthetic/notes/{note_id}/tasks", status_code=status.HTTP_201_CREATED)
def note_to_synthetic_task(note_id: str, payload: SyntheticSourceTodo,
                           ctx: AssistantContext = Depends(context_for),
                           key: str | None = Depends(single_idempotency_key)):
    return {"id": invoke(knowledge_todos.create_from_source, ctx, "note", note_id, payload.title,
                         payload.description, payload.priority, payload.due_at, confirmed_by_user=True, idempotency_key=key)}
