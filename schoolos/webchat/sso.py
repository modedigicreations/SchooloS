"""Verifies the SSO token issued by Modebills' /api/enterprise/schoolos/sso
(see its src/server/jwt.helpers.ts for the matching signer).

Access to a real deployment is meant to come from this — a staff member's
own Modebills login, not a guessable ?school=<slug> URL parameter. The
slug path in app.py stays available as a local/demo fallback only, and is
disabled automatically once SCHOOLOS_SSO_SECRET is configured (see
sso_required()).
"""

import base64
import hashlib
import hmac
import json
import os
import time


class InvalidTokenError(Exception):
    pass


def _b64url_decode(s: str) -> bytes:
    padding = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + padding)


def sso_required() -> bool:
    """True once this deployment is configured for real SSO — the
    ?school=<slug> fallback is then refused rather than silently allowed."""
    return bool(os.environ.get("SCHOOLOS_SSO_SECRET"))


def verify_sso_token(token: str, secret: str | None = None) -> dict:
    """Verify an HS256 JWT and return its claims (organizationId,
    organizationName, organizationSlug, userId, userName, role, exp).
    Raises InvalidTokenError on a bad signature, malformed token, or an
    expired `exp` claim.
    """
    secret = secret or os.environ.get("SCHOOLOS_SSO_SECRET")
    if not secret:
        raise InvalidTokenError("SCHOOLOS_SSO_SECRET is not configured on this server")

    try:
        header_b64, payload_b64, sig_b64 = token.split(".")
    except ValueError:
        raise InvalidTokenError("malformed token")

    expected_sig = hmac.new(
        secret.encode(), f"{header_b64}.{payload_b64}".encode(), hashlib.sha256
    ).digest()
    try:
        actual_sig = _b64url_decode(sig_b64)
    except Exception as exc:
        raise InvalidTokenError("malformed signature") from exc

    if not hmac.compare_digest(expected_sig, actual_sig):
        raise InvalidTokenError("invalid signature")

    try:
        payload = json.loads(_b64url_decode(payload_b64))
    except Exception as exc:
        raise InvalidTokenError("malformed payload") from exc

    exp = payload.get("exp")
    if exp is not None and time.time() > exp:
        raise InvalidTokenError("token expired")

    return payload
