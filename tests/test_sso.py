"""Tests for schoolos.webchat.sso — verifying the HS256 JWT issued by
Modebills' /api/enterprise/schoolos/sso.

_sign (below) is a test-only Python reimplementation of the exact signing
algorithm in Modebills' src/server/jwt.helpers.ts (HS256, base64url without
padding), so these tests prove real cross-language interop, not just that
verify_sso_token agrees with itself.

Run: python -m tests.test_sso
"""

import base64
import hashlib
import hmac
import json
import time

from schoolos.webchat.sso import InvalidTokenError, verify_sso_token

SECRET = "test-sso-secret-at-least-32-chars-long"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _sign(payload: dict, secret: str = SECRET) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    header_b64 = _b64url(json.dumps(header).encode())
    payload_b64 = _b64url(json.dumps(payload).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode()
    sig = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{payload_b64}.{_b64url(sig)}"


def _claims(**overrides) -> dict:
    claims = {
        "organizationId": "org-1",
        "organizationName": "Greenfield Academy",
        "organizationSlug": "greenfield",
        "userId": "user-1",
        "userName": "Mrs. Bello",
        "role": "admin",
        "exp": int(time.time()) + 3600,
    }
    claims.update(overrides)
    return claims


def test_verifies_a_validly_signed_token():
    token = _sign(_claims())
    claims = verify_sso_token(token, SECRET)
    assert claims["organizationId"] == "org-1"
    assert claims["role"] == "admin"


def test_rejects_wrong_secret():
    token = _sign(_claims())
    try:
        verify_sso_token(token, "a-completely-different-secret")
        raise AssertionError("expected InvalidTokenError")
    except InvalidTokenError as exc:
        assert "signature" in str(exc)


def test_rejects_tampered_payload():
    token = _sign(_claims())
    header_b64, payload_b64, sig_b64 = token.split(".")
    tampered_payload = _b64url(json.dumps(_claims(organizationId="org-attacker")).encode())
    tampered = f"{header_b64}.{tampered_payload}.{sig_b64}"
    try:
        verify_sso_token(tampered, SECRET)
        raise AssertionError("expected InvalidTokenError")
    except InvalidTokenError as exc:
        assert "signature" in str(exc)


def test_rejects_expired_token():
    token = _sign(_claims(exp=int(time.time()) - 60))
    try:
        verify_sso_token(token, SECRET)
        raise AssertionError("expected InvalidTokenError")
    except InvalidTokenError as exc:
        assert "expired" in str(exc)


def test_rejects_malformed_token():
    try:
        verify_sso_token("not-a-jwt", SECRET)
        raise AssertionError("expected InvalidTokenError")
    except InvalidTokenError as exc:
        assert "malformed" in str(exc)


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {t.__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    if failures:
        raise SystemExit(1)
