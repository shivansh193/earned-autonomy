import os
from pathlib import Path

import sentry_sdk
from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.middleware.cors import CORSMiddleware

from app.api.main import api_router
from app.core.config import settings

FRONTEND_DIR = Path(__file__).parent / "frontend"


def custom_generate_unique_id(route: APIRoute) -> str:
    return f"{route.tags[0]}-{route.name}"


if settings.SENTRY_DSN and settings.FASTAPI_ENV != "development":
    sentry_sdk.init(dsn=str(settings.SENTRY_DSN), enable_tracing=True)

app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    generate_unique_id_function=custom_generate_unique_id,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_HOST],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.API_V1_STR)

# Earned Autonomy: record real traffic for replay (tools/replay). Off unless set.
if record_file := os.environ.get("EA_RECORD_FILE"):
    from replay.record import RecordMiddleware

    app.add_middleware(
        RecordMiddleware,
        path=record_file,
        known_secrets={"SUPERUSER_PASSWORD": settings.FIRST_SUPERUSER_PASSWORD},
    )

# Earned Autonomy: count calls to vulnerable functions during replay (tools/runtime_hits).
# Off unless both EA_HITS_TARGETS and EA_HITS_TOKEN are set.
if os.environ.get("EA_HITS_TARGETS") and (hits_token := os.environ.get("EA_HITS_TOKEN")):
    from runtime_hits import HitCounter, HitsEndpoint

    hit_counter = HitCounter.from_env()
    if hit_counter is not None:
        app.add_middleware(HitsEndpoint, counter=hit_counter.start(), token=hits_token)
app.frontend("/", directory=FRONTEND_DIR)
