"""Stateless signed URLs for previewing/sharing published Harness HTML artifacts.

Tokens are anonymous *capability* credentials: they bind ``tenant_id``,
``session_id``, ``task_frame_id`` and a normalized ``path`` to a short-lived
HMAC signature and let any bearer (with or without a user session) open that
one artifact inline. Everything except an unknown/invalid/no-longer-valid
token maps to HTTP 404 so the endpoint does not act as an existence or
tenant oracle.
"""

from __future__ import annotations

import base64
import hmac
import json
import time
from typing import Any

from fastapi import HTTPException

from app.harness import HarnessArtifactAccessError, normalize_harness_artifact_path
from app.security import auth as _auth

# Default lifetime of a signed share link.
ARTIFACT_SHARE_TTL_SECONDS = 7 * 24 * 3600
# Marker that distinguishes an artifact-share token from any other
# APP_SECRET-signed token (e.g. a user login token) that `_decode_token`
# would otherwise accept.
ARTIFACT_SHARE_CLAIM = "artifact_share"

_REQUIRED_CLAIMS = ("tenant_id", "session_id", "task_frame_id", "path")


def mint_artifact_share_token(
    *,
    tenant_id: str,
    session_id: str,
    task_frame_id: str,
    path: str,
    ttl_seconds: int = ARTIFACT_SHARE_TTL_SECONDS,
) -> str:
    """Sign an artifact-share capability token for a normalized path.

    The path is normalized *before* signing so that ``reports/./x.html``,
    ``reports\\x.html`` and a traversal like ``../secret`` can never be
    smuggled into a valid token: any path that ``normalize_harness_artifact_path``
    rejects is refused here, and the canonical form is what gets signed.
    """
    now = int(time.time())
    canonical_path = normalize_harness_artifact_path(path)
    ttl = int(ttl_seconds)
    payload = {
        "type": ARTIFACT_SHARE_CLAIM,
        "tenant_id": str(tenant_id).strip(),
        "session_id": str(session_id).strip(),
        "task_frame_id": str(task_frame_id).strip(),
        "path": canonical_path,
        "iat": now,
        "exp": now + ttl,
    }
    body = _auth._b64(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    return f"{body}.{_auth._sign(body)}"


def decode_artifact_share_token(token: str) -> dict[str, Any]:
    """Validate a share token and return its normalized payload.

    Every failure mode — missing separator, bad signature, unparsable payload,
    wrong claim type, missing claims, expired, or an enclosing/traversal path —
    raises HTTP 404. The returned ``path`` is the re-normalized canonical form.
    """
    if not isinstance(token, str) or not token.strip():
        raise _raise_404()
    try:
        body, signature = token.split(".", 1)
    except ValueError:
        raise _raise_404() from None
    if not hmac.compare_digest(_auth._sign(body), signature):
        raise _raise_404()
    try:
        payload = json.loads(
            base64.urlsafe_b64decode(_auth._pad_b64(body)).decode("utf-8")
        )
    except ValueError:
        # binascii.Error, json.JSONDecodeError and UnicodeDecodeError are all
        # ValueError subclasses.
        raise _raise_404() from None
    if not isinstance(payload, dict):
        raise _raise_404()
    if payload.get("type") != ARTIFACT_SHARE_CLAIM:
        raise _raise_404()
    for claim in _REQUIRED_CLAIMS:
        if not isinstance(payload.get(claim), str) or not payload[claim].strip():
            raise _raise_404()
    if int(payload.get("exp", 0)) < int(time.time()):
        raise _raise_404()
    try:
        canonical = normalize_harness_artifact_path(payload["path"])
    except HarnessArtifactAccessError:
        raise _raise_404() from None
    payload["path"] = canonical
    return payload


def _raise_404() -> HTTPException:
    return HTTPException(status_code=404, detail="Artifact share link not found")


__all__ = [
    "ARTIFACT_SHARE_CLAIM",
    "ARTIFACT_SHARE_TTL_SECONDS",
    "decode_artifact_share_token",
    "mint_artifact_share_token",
]