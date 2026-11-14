"""Jarvis synthetic model endpoints."""
from fastapi import APIRouter
from fastapi import Depends, status
from ...security.assistant_context import AssistantContext
from ...services.synthetic_model_gateway import SyntheticModelGateway
from ...services.jarvis_reply_draft_workflow import JarvisReplyDraftWorkflow
from .routes import context_for, invoke
from .schemas import SyntheticDraftWrite, SyntheticProposalApproval, SyntheticModelRequestCreate

router = APIRouter()
fake_model = SyntheticModelGateway()
reply_drafts = JarvisReplyDraftWorkflow()


@router.post("/synthetic/model/requests", status_code=status.HTTP_201_CREATED)
def prepare_fake_model_request(payload: SyntheticModelRequestCreate, ctx: AssistantContext = Depends(context_for)):
    result = invoke(fake_model.prepare, ctx, payload.purpose, payload.source_id)
    return {**result, "simulated": True}


@router.get("/synthetic/model/requests/{request_id}")
def get_fake_model_request(request_id: str, ctx: AssistantContext = Depends(context_for)):
    return {**invoke(fake_model.get_request, ctx, request_id), "simulated": True}


@router.post("/synthetic/model/requests/{request_id}/cancel")
def cancel_fake_model_request(request_id: str, ctx: AssistantContext = Depends(context_for)):
    invoke(fake_model.cancel, ctx, request_id)
    return {"status": "cancelled", "simulated": True}


@router.post("/synthetic/model/requests/{request_id}/execute")
def run_fake_model_request(request_id: str, payload: SyntheticProposalApproval, ctx: AssistantContext = Depends(context_for)):
    return invoke(fake_model.execute, ctx, request_id, payload.confirmed_hash, confirmed_by_user=True)


@router.get("/synthetic/model/suggestions/{suggestion_id}")
def get_fake_suggestion(suggestion_id: str, ctx: AssistantContext = Depends(context_for)):
    return {**invoke(fake_model.get_suggestion, ctx, suggestion_id), "simulated": True}


@router.post("/synthetic/model/suggestions/{suggestion_id}/reject")
def reject_fake_suggestion(suggestion_id: str, ctx: AssistantContext = Depends(context_for)):
    invoke(fake_model.reject_suggestion, ctx, suggestion_id, confirmed_by_user=True)
    return {"status": "rejected", "simulated": True}


@router.post("/synthetic/model/suggestions/{suggestion_id}/drafts", status_code=status.HTTP_201_CREATED)
def fake_suggestion_to_draft(suggestion_id: str, payload: SyntheticDraftWrite, ctx: AssistantContext = Depends(context_for)):
    return {"id": invoke(reply_drafts.accept_as_draft, ctx, suggestion_id, **payload.model_dump(), confirmed_by_user=True), "synthetic": True}
