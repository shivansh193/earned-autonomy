from typing import Annotated

from fastapi import Depends

from shop import security


class Session:
    def query(self, q):
        return q


def get_db():
    yield Session()


def get_current_user(token: str = "") -> dict:
    return security.read_token(token)


SessionDep = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[dict, Depends(get_current_user)]
