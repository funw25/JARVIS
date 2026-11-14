"""Jarvis synthetic overview endpoints."""
from fastapi import APIRouter
from fastapi import Depends, Query
from ...security.assistant_context import AssistantContext
from ...services.jarvis_overview_service import JarvisOverviewService
from ...services.jarvis_search_service import JarvisSearchService
from ...services.jarvis_task_query_service import JarvisTaskQueryService
from ...services.synthetic_knowledge_query_service import SyntheticKnowledgeQueryService
from .routes import context_for, invoke

router = APIRouter()
overview = JarvisOverviewService()
search_service = JarvisSearchService()
task_search = JarvisTaskQueryService()
knowledge_query = SyntheticKnowledgeQueryService()


@router.get("/overview")
def get_overview(utc_offset_minutes: int = Query(default=0, ge=-720, le=840),
                 limit: int = Query(default=5, ge=1, le=50),
                 ctx: AssistantContext = Depends(context_for)):
    return invoke(overview.snapshot, ctx, utc_offset_minutes=utc_offset_minutes, limit=limit)


@router.get("/search")
def search(query: str = Query(min_length=1, max_length=200),
           kind: list[str] | None = Query(default=None),
           limit: int = Query(default=25, ge=1, le=100),
           ctx: AssistantContext = Depends(context_for)):
    kinds = tuple(kind) if kind is not None else ("mail", "todo", "capture", "note")
    return invoke(search_service.search, ctx, query, kinds=kinds, limit=limit)


@router.get("/tasks")
def search_tasks(query: str = Query(default="", max_length=200),
                 status: list[str] | None = Query(default=None),
                 priority: str | None = Query(default=None),
                 due_from: str | None = Query(default=None),
                 due_before: str | None = Query(default=None),
                 limit: int = Query(default=25, ge=1, le=100),
                 cursor: str | None = Query(default=None, max_length=1024),
                 ctx: AssistantContext = Depends(context_for)):
    statuses = tuple(status) if status is not None else ("open", "in_progress")
    return invoke(task_search.search, ctx, query=query, statuses=statuses, priority=priority,
                  due_from=due_from, due_before=due_before, limit=limit, cursor=cursor)


@router.get("/knowledge/{kind}")
def list_synthetic_knowledge(kind: str, query: str = Query(default="", max_length=200),
                             tag: str | None = Query(default=None, min_length=1, max_length=64),
                             status: str | None = Query(default=None),
                             limit: int = Query(default=25, ge=1, le=100),
                             cursor: str | None = Query(default=None, max_length=1024),
                             ctx: AssistantContext = Depends(context_for)):
    return invoke(knowledge_query.search, ctx, kind, query=query, tag=tag, status=status, limit=limit, cursor=cursor)
