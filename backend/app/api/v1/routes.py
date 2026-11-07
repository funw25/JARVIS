from fastapi import APIRouter, Depends, Header, HTTPException, status

from ...security.assistant_context import AccessDenied, AssistantContext, InvalidTransition, NotFound
from ...security.local_auth import authenticated_owner, assistant_context
from ...services.conversation_service import ConversationService, ContinuityService
from ...services.memory_gateway import MemoryGateway
from ...services.todo_service import TodoService
from .schemas import (ConversationCreate, ContinuityAccept, ContinuityDraftCreate, MemoryCandidateCreate, MemoryShareCreate, MessageCreate, TodoCreate, TodoShareCreate, TodoStatusChange, TodoUpdate)

router = APIRouter(tags=["local-core"])
conversations = ConversationService()
continuity = ContinuityService()
memories = MemoryGateway()
todos = TodoService()

def context_for(owner: str = Depends(authenticated_owner), assistant_id: str = Header(alias="X-Jarvis-Assistant")) -> AssistantContext:
    return assistant_context(owner, assistant_id)

def invoke(callable_, *args, **kwargs):
    try:
        return callable_(*args, **kwargs)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AccessDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

@router.get("/assistants")
def list_assistants(_: str = Depends(authenticated_owner)):
    return [{"assistant_id":"companion","enabled":True},{"assistant_id":"jarvis","enabled":True}]

@router.get("/conversations")
def list_conversations(ctx: AssistantContext = Depends(context_for)):
    return [{k:v for k,v in item.items() if k != "owner_id"} for item in invoke(conversations.list_for_context, ctx)]

@router.post("/conversations", status_code=status.HTTP_201_CREATED)
def create_conversation(payload: ConversationCreate, ctx: AssistantContext = Depends(context_for)):
    return {"id": invoke(conversations.create, ctx, payload.title)}

@router.get("/conversations/{conversation_id}/messages")
def get_messages(conversation_id: str, ctx: AssistantContext = Depends(context_for)):
    return invoke(conversations.history, ctx, conversation_id)

@router.post("/conversations/{conversation_id}/messages", status_code=status.HTTP_201_CREATED)
def add_message(conversation_id: str, payload: MessageCreate, ctx: AssistantContext = Depends(context_for)):
    return {"id": invoke(conversations.append_message, ctx, conversation_id, payload.role, payload.body, payload.source_refs)}

@router.get("/memories")
def list_memories(ctx: AssistantContext = Depends(context_for)):
    return invoke(memories.list_visible, ctx)

@router.post("/memories/candidates", status_code=status.HTTP_201_CREATED)
def create_memory_candidate(payload: MemoryCandidateCreate, ctx: AssistantContext = Depends(context_for)):
    refs = [source.model_dump() for source in payload.source_refs]
    return {"id": invoke(memories.create_candidate, ctx, payload.category, payload.content, refs, payload.confidence)}

@router.post("/memories/{memory_id}/approve")
def approve_memory(memory_id: str, ctx: AssistantContext = Depends(context_for)):
    invoke(memories.approve, ctx, memory_id)
    return {"status":"approved"}

@router.post("/memories/{memory_id}/forget")
def forget_memory(memory_id: str, ctx: AssistantContext = Depends(context_for)):
    invoke(memories.forget, ctx, memory_id)
    return {"status":"forgotten"}

@router.delete("/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_memory(memory_id: str, ctx: AssistantContext = Depends(context_for)):
    invoke(memories.delete, ctx, memory_id)
    return None

@router.post("/memory-shares", status_code=status.HTTP_201_CREATED)
def share_memory(payload: MemoryShareCreate, ctx: AssistantContext = Depends(context_for)):
    return {"id":invoke(memories.grant, ctx, payload.target_assistant, memory_id=payload.memory_id, namespace_id=payload.namespace_id)}

@router.delete("/memory-shares/{grant_id}")
def revoke_memory_share(grant_id: str, ctx: AssistantContext = Depends(context_for)):
    invoke(memories.revoke_grant, ctx, grant_id)
    return {"status":"revoked"}

@router.post("/continuity/drafts", status_code=status.HTTP_201_CREATED)
def create_continuity_draft(payload: ContinuityDraftCreate, ctx: AssistantContext = Depends(context_for)):
    return {"id":invoke(continuity.create_draft, ctx, payload.conversation_id, payload.summary, payload.topics, payload.open_items, payload.source_refs)}

@router.post("/continuity/{snapshot_id}/discard")
def discard_continuity(snapshot_id: str, ctx: AssistantContext = Depends(context_for)):
    invoke(continuity.discard, ctx, snapshot_id)
    return {"status":"discarded"}

@router.post("/continuity/{snapshot_id}/accept", status_code=status.HTTP_201_CREATED)
def accept_continuity(snapshot_id: str, payload: ContinuityAccept, ctx: AssistantContext = Depends(context_for)):
    conversation_id, snapshot = invoke(continuity.accept_and_start, ctx, snapshot_id, payload.title)
    return {"conversation_id":conversation_id,"continuity":snapshot}

@router.get("/todos")
def list_todos(ctx: AssistantContext = Depends(context_for)):
    return [{k:v for k,v in item.items() if k != "owner_id"} for item in invoke(todos.list, ctx)]

def single_idempotency_key(values: list[str] | None = Header(default=None, alias="Idempotency-Key")) -> str | None:
    if values is not None and len(values) != 1:
        raise HTTPException(status_code=422, detail="Provide only one Idempotency-Key header")
    return values[0] if values else None


@router.post("/todos", status_code=status.HTTP_201_CREATED)
def create_todo(payload: TodoCreate, ctx: AssistantContext = Depends(context_for),
                key: str | None = Depends(single_idempotency_key)):
    sources = [source.model_dump() for source in payload.sources]
    return {"id":invoke(todos.create, ctx, payload.title, payload.description, payload.priority, payload.due_at, sources, confirmed_by_user=True, idempotency_key=key)}

@router.get("/todos/{todo_id}")
def get_todo(todo_id: str, ctx: AssistantContext = Depends(context_for)):
    item = invoke(todos.get, ctx, todo_id)
    item.pop("owner_id", None)
    return item

@router.patch("/todos/{todo_id}")
def update_todo(todo_id: str, payload: TodoUpdate, ctx: AssistantContext = Depends(context_for)):
    values = payload.model_dump(exclude_unset=True)
    invoke(todos.update, ctx, todo_id, **values)
    item = invoke(todos.get, ctx, todo_id)
    item.pop("owner_id", None)
    return item

@router.post("/todos/{todo_id}/status")
def set_todo_status(todo_id: str, payload: TodoStatusChange, ctx: AssistantContext = Depends(context_for)):
    invoke(todos.set_status, ctx, todo_id, payload.status)
    item = invoke(todos.get, ctx, todo_id)
    item.pop("owner_id", None)
    return item

@router.delete("/todos/{todo_id}")
def delete_todo(todo_id: str, ctx: AssistantContext = Depends(context_for)):
    invoke(todos.delete, ctx, todo_id)
    return {"status":"deleted","recoverable":True}

@router.post("/todos/{todo_id}/restore")
def restore_todo(todo_id: str, ctx: AssistantContext = Depends(context_for)):
    invoke(todos.restore, ctx, todo_id)
    return {"status":"restored"}

@router.post("/todos/{todo_id}/shares", status_code=status.HTTP_201_CREATED)
def share_todo(todo_id: str, payload: TodoShareCreate, ctx: AssistantContext = Depends(context_for)):
    grant_id = invoke(todos.share_readonly, ctx, todo_id, payload.target_assistant, confirmed_by_user=True)
    return {"id":grant_id,"access":"read_only"}

@router.delete("/todo-shares/{grant_id}")
def revoke_todo_share(grant_id: str, ctx: AssistantContext = Depends(context_for)):
    invoke(todos.revoke_share, ctx, grant_id)
    return {"status":"revoked"}
