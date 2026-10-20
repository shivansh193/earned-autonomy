from .algorithms import verify_signature


class PyJWT:
    def encode(self, payload, key):
        return "t"

    def decode(self, token, key):
        verify_signature(token, key)
        return {}


_jwt_global_obj = PyJWT()
encode = _jwt_global_obj.encode
decode = _jwt_global_obj.decode
