from fastapi import APIRouter

from shop.deps import CurrentUser, SessionDep
from shop.helpers import Formatter

router = APIRouter(prefix="/items", tags=["items"])


@router.get("/")
def list_items(session: SessionDep, user: CurrentUser, limit: int = 100):
    rows = session.query("items")
    fmt = Formatter()
    return fmt.render(rows)


@router.post("/import")
def import_items(payload: str, loader=None):
    return loader.load(payload)
