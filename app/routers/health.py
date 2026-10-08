from datetime import datetime, timezone
from fastapi import APIRouter

router = APIRouter(tags=["Health"])

@router.get("/health")
def health_check():
    return {
        "status": "UP",
        "service": "NationalDepo",
        "description": "Simulated Depository Sandbox",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
