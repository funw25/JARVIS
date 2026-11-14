"""Jarvis synthetic reminders endpoints."""
from fastapi import APIRouter
from fastapi import Depends, Query, status
from ...security.assistant_context import AssistantContext
from ...services.synthetic_reminder_service import SyntheticReminderService
from .routes import context_for, invoke
from .schemas import SyntheticReminderCreate, SyntheticReminderEnabled, SyntheticReminderAck

router = APIRouter()
reminders = SyntheticReminderService()


@router.get("/synthetic/reminders/rules")
def list_synthetic_reminder_rules(ctx: AssistantContext = Depends(context_for)):
    return {"items": invoke(reminders.list_rules, ctx), "synthetic": True}


@router.post("/synthetic/reminders/rules", status_code=status.HTTP_201_CREATED)
def create_synthetic_reminder_rule(payload: SyntheticReminderCreate, ctx: AssistantContext = Depends(context_for)):
    return {"id": invoke(reminders.create_rule, ctx, payload.todo_id, payload.offset_minutes, confirmed_by_user=True), "synthetic": True}


@router.patch("/synthetic/reminders/rules/{rule_id}/enabled")
def set_synthetic_reminder_enabled(rule_id: str, payload: SyntheticReminderEnabled, ctx: AssistantContext = Depends(context_for)):
    invoke(reminders.set_enabled, ctx, rule_id, payload.enabled, confirmed_by_user=True)
    return {"enabled": payload.enabled, "synthetic": True}


@router.get("/synthetic/reminders/due")
def get_due_synthetic_reminders(limit: int = Query(default=25, ge=1, le=100), ctx: AssistantContext = Depends(context_for)):
    return {**invoke(reminders.due, ctx, limit=limit), "synthetic": True}


@router.post("/synthetic/reminders/{rule_id}/acknowledge")
def acknowledge_synthetic_reminder(rule_id: str, payload: SyntheticReminderAck, ctx: AssistantContext = Depends(context_for)):
    return {**invoke(reminders.acknowledge, ctx, rule_id, payload.token, confirmed_by_user=True), "synthetic": True}
