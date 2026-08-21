"""健康检查路由。"""
from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check():
    return {"status": "healthy", "service": "review-service", "version": "1.0.0"}
