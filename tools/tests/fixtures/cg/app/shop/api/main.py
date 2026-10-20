from fastapi import APIRouter

from shop.api.routes import health, items

api_router = APIRouter()
api_router.include_router(items.router)
api_router.include_router(health.router)
