"""Jarvis synthetic mail rules endpoints."""
from fastapi import APIRouter
from fastapi import Depends, status
from ...security.assistant_context import AssistantContext
from ...services.synthetic_mail_rule_service import SyntheticMailRuleService
from .routes import context_for, invoke
from .schemas import SyntheticReminderEnabled, SyntheticMailRuleCreate, SyntheticRuleApproval

router = APIRouter()
mail_rules = SyntheticMailRuleService()


@router.get("/synthetic/mail-rules")
def list_synthetic_mail_rules(ctx: AssistantContext = Depends(context_for)):
    return {"items": invoke(mail_rules.list_rules, ctx), "synthetic": True}


@router.post("/synthetic/mail-rules", status_code=status.HTTP_201_CREATED)
def create_synthetic_mail_rule(payload: SyntheticMailRuleCreate, ctx: AssistantContext = Depends(context_for)):
    return {"id": invoke(mail_rules.create, ctx, **payload.model_dump(), confirmed_by_user=True), "synthetic": True}


@router.patch("/synthetic/mail-rules/{rule_id}/enabled")
def set_synthetic_mail_rule_enabled(rule_id: str, payload: SyntheticReminderEnabled, ctx: AssistantContext = Depends(context_for)):
    invoke(mail_rules.set_enabled, ctx, rule_id, payload.enabled, confirmed_by_user=True)
    return {"enabled": payload.enabled, "synthetic": True}


@router.post("/synthetic/mail-rules/{rule_id}/previews", status_code=status.HTTP_201_CREATED)
def create_synthetic_mail_rule_preview(rule_id: str, ctx: AssistantContext = Depends(context_for)):
    return {**invoke(mail_rules.preview, ctx, rule_id), "synthetic": True}


@router.get("/synthetic/mail-rule-previews/{preview_id}")
def get_synthetic_mail_rule_preview(preview_id: str, ctx: AssistantContext = Depends(context_for)):
    return {**invoke(mail_rules.get_preview, ctx, preview_id), "synthetic": True}


@router.post("/synthetic/mail-rule-previews/{preview_id}/apply")
def apply_synthetic_mail_rule_preview(preview_id: str, payload: SyntheticRuleApproval, ctx: AssistantContext = Depends(context_for)):
    return {"changed": invoke(mail_rules.apply, ctx, preview_id, payload.confirmed_hash, confirmed_by_user=True), "synthetic": True}


@router.post("/synthetic/mail-rule-previews/{preview_id}/undo")
def undo_synthetic_mail_rule_preview(preview_id: str, ctx: AssistantContext = Depends(context_for)):
    return {**invoke(mail_rules.undo, ctx, preview_id, confirmed_by_user=True), "synthetic": True}
