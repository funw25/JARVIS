from fastapi import APIRouter

router = APIRouter(tags=["system"])

@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "jarvis-local-core", "data_mode": "synthetic-only"}
