"""Jarvis synthetic knowledge tags endpoints."""
from fastapi import APIRouter
from fastapi import Depends, Query
from ...security.assistant_context import AssistantContext
from ...services.synthetic_knowledge_tag_service import SyntheticKnowledgeTagService
from .routes import context_for, invoke
from .schemas import SyntheticKnowledgeTagWrite

router = APIRouter()
knowledge_tags = SyntheticKnowledgeTagService()


@router.get("/synthetic/knowledge-tags/search")
def search_synthetic_knowledge_tags(tag: str = Query(min_length=1, max_length=64),
                                    limit: int = Query(default=25, ge=1, le=100),
                                    ctx: AssistantContext = Depends(context_for)):
    return {**invoke(knowledge_tags.find, ctx, tag, limit=limit), "synthetic": True}


@router.get("/synthetic/knowledge-tags/{kind}/{object_id}")
def list_synthetic_knowledge_tags(kind: str, object_id: str, ctx: AssistantContext = Depends(context_for)):
    return {"items": invoke(knowledge_tags.list_for_item, ctx, kind, object_id), "synthetic": True}


@router.post("/synthetic/knowledge-tags/{kind}/{object_id}")
def add_synthetic_knowledge_tag(kind: str, object_id: str, payload: SyntheticKnowledgeTagWrite,
                                ctx: AssistantContext = Depends(context_for)):
    return {**invoke(knowledge_tags.add, ctx, kind, object_id, payload.tag, confirmed_by_user=True), "synthetic": True}


@router.post("/synthetic/knowledge-tags/{kind}/{object_id}/remove")
def remove_synthetic_knowledge_tag(kind: str, object_id: str, payload: SyntheticKnowledgeTagWrite,
                                   ctx: AssistantContext = Depends(context_for)):
    return {**invoke(knowledge_tags.remove, ctx, kind, object_id, payload.tag, confirmed_by_user=True), "synthetic": True}
