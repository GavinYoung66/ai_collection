"""健康检查：仅服务根 GET /health（不在 /api/v1 下）。"""
from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check():
    return {"status": "healthy", "service": "qc-service", "version": "1.0.0"}
