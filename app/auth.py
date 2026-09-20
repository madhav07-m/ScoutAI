"""
Authentication — verifies Supabase Auth JWTs.

Supabase Auth (email/password + Google sign-in, both configured in the
Supabase dashboard) issues a JWT to the frontend on login. The
frontend sends it back on every API call as:
    Authorization: Bearer <token>
This module verifies that token server-side and extracts the user's
id/email from it -- the backend never handles passwords or OAuth
itself, Supabase does all of that; this only checks "is this a
genuine, unexpired token Supabase issued."

Verification method: Supabase's JWKS endpoint (public keys), NOT a
shared secret. Newer Supabase projects sign tokens with an asymmetric
key (ECC P-256 / ES256) rather than the old shared HS256 secret, so
verifying against a single secret string doesn't work here -- fetching
the current public signing key from Supabase's own JWKS endpoint does,
and works the same way whether the project uses the old or new key
type, so nothing needs to change here if that ever rotates.

Requires SUPABASE_URL in the environment (e.g.
https://itlxwaictkckmuxpxvxs.supabase.co) -- this is NOT a secret, it's
the same URL already used in the frontend's Supabase client, just also
needed here to build the JWKS URL.
"""

import os
from functools import lru_cache

import jwt
from fastapi import Header, HTTPException
from jwt import PyJWKClient

_SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")


@lru_cache(maxsize=1)
def _get_jwks_client() -> PyJWKClient:
    if not _SUPABASE_URL:
        raise HTTPException(
            500,
            "Server is not configured for authentication -- SUPABASE_URL "
            "is not set in the environment.",
        )
    jwks_url = f"{_SUPABASE_URL}/auth/v1/.well-known/jwks.json"
    return PyJWKClient(jwks_url)


class CurrentUser:
    def __init__(self, user_id: str, email: str):
        self.user_id = user_id
        self.email = email


def _decode_token(token: str) -> dict:
    try:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256", "RS256", "HS256"],  # accepts whichever
            # algorithm this project's current key actually uses, so this
            # keeps working across a key rotation without a code change
            audience="authenticated",  # Supabase sets this audience claim by default
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Session expired -- please sign in again.")
    except jwt.PyJWKClientError as e:
        raise HTTPException(500, f"Could not fetch Supabase signing key: {e}")
    except jwt.InvalidTokenError as e:
        raise HTTPException(401, f"Invalid auth token: {e}")
    return payload


def get_current_user(authorization: str = Header(None)) -> CurrentUser:
    """FastAPI dependency: require a valid, logged-in user."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or malformed Authorization header -- please sign in.")
    token = authorization.removeprefix("Bearer ").strip()
    payload = _decode_token(token)
    user_id = payload.get("sub")
    email = payload.get("email", "")
    if not user_id:
        raise HTTPException(401, "Token did not contain a user id.")
    return CurrentUser(user_id=user_id, email=email)


def get_current_user_optional(authorization: str = Header(None)):
    """Same as get_current_user, but returns None instead of raising
    when no token is present or it's invalid -- for endpoints that work
    for both logged-in and anonymous users."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = _decode_token(token)
    except HTTPException:
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    return CurrentUser(user_id=user_id, email=payload.get("email", ""))