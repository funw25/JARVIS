"""Compose Jarvis routes in their original registration order."""
from fastapi import APIRouter

from .jarvis_overview_routes import router as overview_router
from .jarvis_mail_routes import router as mail_router
from .jarvis_knowledge_routes import router as knowledge_router
from .jarvis_model_routes import router as model_router
from .jarvis_reminders_routes import router as reminders_router
from .jarvis_mail_rules_routes import router as mail_rules_router
from .jarvis_knowledge_tags_routes import router as knowledge_tags_router

router = APIRouter(prefix="/jarvis", tags=["jarvis-synthetic"])
router.include_router(overview_router)
router.include_router(mail_router)
router.include_router(knowledge_router)
router.include_router(model_router)
router.include_router(reminders_router)
router.include_router(mail_rules_router)
router.include_router(knowledge_tags_router)
