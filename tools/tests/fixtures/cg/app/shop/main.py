from fastapi import FastAPI

from shop.api.main import api_router
from shop.config import settings

app = FastAPI()
app.include_router(api_router, prefix=settings.API_V1_STR)
