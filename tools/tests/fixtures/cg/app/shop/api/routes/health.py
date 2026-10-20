from fastapi import APIRouter

from shop import yamlish_user

router = APIRouter()


@router.get("/health")
def health():
    return {"ok": True}


def unused_admin_tool(data):
    return yamlish_user.parse(data)
