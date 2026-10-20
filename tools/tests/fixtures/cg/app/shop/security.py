import fakejwt

from shop.config import settings


def read_token(token: str) -> dict:
    return fakejwt.decode(token, settings.SECRET)


def make_token(sub: str) -> str:
    return fakejwt.encode({"sub": sub}, settings.SECRET)
