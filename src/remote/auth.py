"""
JWT authentication helpers for the Chad Brain server.

CHAD_AUTH_MODE env var controls which auth method the WS handler accepts:
  "token"  (default) — shared CHAD_SERVER_TOKEN secret (personal / self-hosted)
  "jwt"              — Supabase Auth JWTs only (multi-user / hosted Brain)
  "hybrid"           — accepts either CHAD_SERVER_TOKEN (desktop Limb) or a
                       valid Supabase JWT (browser/PWA); use this when the same
                       Brain serves both the local desktop and remote browser clients

In jwt/hybrid mode the Brain fetches Supabase's JWKS endpoint on first use and
caches the signing keys in process — no shared secret needed.  SUPABASE_PROJECT_REF
(or SUPABASE_URL) must be set so the JWKS URL can be derived.
The verified user_id (sub claim) is returned and threaded through downstream
calls for per-tenant data isolation.
"""
import os
import logging
from typing import Optional, Tuple

logger = logging.getLogger("ChadAuth")

_AUTH_MODE: Optional[str] = None
_JWKS_CLIENT = None   # jwt.PyJWKClient, lazily initialised

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def is_loopback_host(host: Optional[str]) -> bool:
    """True when a connection originates from this same machine.

    The desktop Limb and its Brain always run on the same box — a shared
    CHAD_SERVER_TOKEN protects the port from other machines/processes, not
    from the local GUI it's paired with, so loopback callers skip the token
    check entirely (see verify_token / endpoints._check_update_token).
    """
    return host in _LOOPBACK_HOSTS


def get_auth_mode() -> str:
    global _AUTH_MODE
    if _AUTH_MODE is None:
        _AUTH_MODE = os.getenv("CHAD_AUTH_MODE", "token").lower()
    return _AUTH_MODE


def _jwks_client():
    """Return a cached PyJWKClient pointed at the project's JWKS endpoint."""
    global _JWKS_CLIENT
    if _JWKS_CLIENT is not None:
        return _JWKS_CLIENT

    import jwt as pyjwt  # PyJWT >= 2.4

    # Accept either a full URL or a bare project ref
    supabase_url = os.getenv("SUPABASE_URL") or os.getenv("SUPABASE_PROJECT_URL")
    if not supabase_url:
        ref = os.getenv("SUPABASE_PROJECT_REF", "sdkidlgqagwoletstmcr")
        supabase_url = f"https://{ref}.supabase.co"

    supabase_url = supabase_url.rstrip("/")
    jwks_url = f"{supabase_url}/auth/v1/.well-known/jwks.json"
    logger.info(f"[ AUTH ] JWKS endpoint: {jwks_url}")
    _JWKS_CLIENT = pyjwt.PyJWKClient(jwks_url, cache_keys=True)
    return _JWKS_CLIENT


def verify_token(
    auth_token: str, server_token: Optional[str], client_host: Optional[str] = None
) -> Tuple[bool, Optional[str]]:
    """
    Verify an inbound AUTH message token.

    Returns (is_valid, user_id).
    user_id is None in token/shared-secret mode; it's the Supabase UUID in jwt/hybrid mode.
    """
    mode = get_auth_mode()

    # token/hybrid modes protect the port from other machines, not from the
    # local desktop GUI paired with this Brain — skip the check for loopback
    # callers regardless of whether a server_token is configured. jwt mode is
    # explicitly multi-user/hosted, so it always verifies (no bypass).
    if mode in ("token", "hybrid") and is_loopback_host(client_host):
        return True, None

    if mode == "jwt":
        if not auth_token:
            return False, None
        return _verify_supabase_jwt(auth_token)

    if mode == "hybrid":
        # Desktop Limb sends the shared server token; browser/PWA sends a Supabase JWT.
        # Accept whichever matches — server token check is fast and runs first.
        if server_token and auth_token == server_token:
            return True, None
        if not auth_token:
            return False, None
        return _verify_supabase_jwt(auth_token)

    # Default: shared-secret mode ("personal / self-hosted"). Loopback callers
    # (the desktop Limb) already returned True above, so reaching here means
    # a non-loopback caller. ChadServer.__init__ (src/remote/server.py)
    # generates and persists a CHAD_SERVER_TOKEN on first boot if one isn't
    # configured, so server_token should never actually be empty here — but
    # fail closed (deny) rather than allow-all if it somehow is, since this
    # port is bound to 0.0.0.0 and reachable off-box.
    if not server_token:
        return False, None
    return auth_token == server_token, None


def _verify_supabase_jwt(token: str) -> Tuple[bool, Optional[str]]:
    """Verify a Supabase-issued JWT via JWKS and extract the user UUID from sub."""
    try:
        import jwt as pyjwt  # PyJWT

        client = _jwks_client()
        signing_key = client.get_signing_key_from_jwt(token)
        payload = pyjwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "ES256", "HS256"],
            options={"verify_aud": False},
        )
        user_id: Optional[str] = payload.get("sub")
        if not user_id:
            logger.warning("[ AUTH ] JWT valid but missing 'sub' claim")
            return False, None
        return True, user_id
    except Exception as exc:
        logger.warning(f"[ AUTH ] JWT verification failed: {exc}")
        return False, None
