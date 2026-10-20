"""Remove secrets from recorded traffic before it is written to disk."""

from __future__ import annotations

import hashlib
from typing import Any

SECRET_KEYS = {"password", "new_password", "current_password", "client_secret", "token"}
REDACT_RESPONSE_KEYS = {"access_token", "refresh_token"}


def _placeholder(value: str, known: dict[str, str]) -> str:
    for name, secret in known.items():
        if value == secret:
            return "${" + name + "}"
    return "${secret:" + hashlib.sha256(value.encode()).hexdigest()[:12] + "}"


def sanitize_request_body(body: Any, known_secrets: dict[str, str]) -> Any:
    """Replace values of secret-looking fields with placeholders.

    ``known_secrets`` maps a placeholder name to a value the replayer will be given
    (e.g. ``{"SUPERUSER_PASSWORD": "..."}``); any other secret becomes
    ``${secret:<hash>}`` and is replayed as a deterministic dummy, so a signup
    followed by a login with the same password still works.
    """
    if isinstance(body, dict):
        out = {}
        for k, v in body.items():
            if k in SECRET_KEYS and isinstance(v, str):
                out[k] = _placeholder(v, known_secrets)
            else:
                out[k] = sanitize_request_body(v, known_secrets)
        return out
    if isinstance(body, list):
        return [sanitize_request_body(v, known_secrets) for v in body]
    return body


def sanitize_response(body: Any) -> Any:
    if isinstance(body, dict):
        return {
            k: ("<redacted>" if k in REDACT_RESPONSE_KEYS else sanitize_response(v))
            for k, v in body.items()
        }
    if isinstance(body, list):
        return [sanitize_response(v) for v in body]
    return body
