"""Bearer tokens. The server stores only a hash; the role comes from the identity row."""

import hashlib
import hmac
import os

DEV_SECRET = "northlight-dev-only"


def _secret() -> bytes:
    return os.environ.get("VESTLY_TOKEN_SECRET", DEV_SECRET).encode("utf-8")


def token_for(handle: str) -> str:
    """The token issued to an identity. Each process is handed only its own."""
    digest = hmac.new(_secret(), handle.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"vst_{digest[:40]}"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
