"""Stateless signed URLs for previewing/sharing published workspace artifacts.

Tokens are anonymous *capability* credentials: they bind ``tenant_id``,
``session_id``, an owning ``(owner_kind, owner_id)`` pair and a normalized ``path``
to a short-lived HMAC signature, and let any bearer (with or without a user session)
open that one artifact inline. Everything except an unknown/invalid/no-longer-valid
token maps to HTTP 404 so the endpoint does not act as an existence or tenant oracle.

The owner pair is deliberately opaque here. This module only checks that
``owner_kind`` is a well-formed slug; whether the owner exists, belongs to the
session and actually published the path is decided by
``app.core.artifact_owners.resolve_artifact_owner``. Keeping it that way is what
stops ``app.security`` from depending on ``app.core``, and stops a
signature-verification module from consulting a business registry to decide whether
a token is authentic.
"""

from __future__ import annotations

import base64
import hmac
import json
import re
import time
from typing import Any

from fastapi import HTTPException

from app.harness import (
    ARTIFACT_OWNER_DEFAULT_KIND,
    HarnessArtifactAccessError,
    normalize_harness_artifact_path,
)
from app.security import auth as _auth

# Default lifetime of a signed share link.
ARTIFACT_SHARE_TTL_SECONDS = 7 * 24 * 3600
# Marker that distinguishes an artifact-share token from any other
# APP_SECRET-signed token (e.g. a user login token) that `_decode_token`
# would otherwise accept.
ARTIFACT_SHARE_CLAIM = "artifact_share"
# Owner kinds are slugs, never free text: the value is compared against a resolver
# registry and is echoed into error-free 404s only, so a charset check is enough.
# The anchored form is for request models (which match with search semantics); token
# decoding uses `fullmatch` on the body instead, because Python's `$` also matches
# just before a trailing newline.
_OWNER_KIND_BODY = r"[a-z][a-z0-9_]{0,31}"
OWNER_KIND_PATTERN = rf"^{_OWNER_KIND_BODY}$"

_REQUIRED_CLAIMS = ("tenant_id", "session_id", "path")
_OWNER_KIND_RE = re.compile(_OWNER_KIND_BODY)


def normalize_owner_kind(owner_kind: str | None) -> str:
    """Return a well-formed owner-kind slug, or raise ``ValueError``.

    Used by :func:`mint_artifact_share_token` (where a bad value is a caller bug) and
    by the request models. Decoding uses :data:`_OWNER_KIND_RE` directly, because a
    bad value in an untrusted token must be a 404 rather than an exception.
    """

    kind = str(owner_kind or "").strip().lower() or ARTIFACT_OWNER_DEFAULT_KIND
    if not _OWNER_KIND_RE.fullmatch(kind):
        raise ValueError("Artifact owner kind must be a lowercase slug.")
    return kind


def mint_artifact_share_token(
    *,
    tenant_id: str,
    session_id: str,
    path: str,
    owner_kind: str = ARTIFACT_OWNER_DEFAULT_KIND,
    owner_id: str | None = None,
    task_frame_id: str | None = None,
    ttl_seconds: int = ARTIFACT_SHARE_TTL_SECONDS,
) -> str:
    """Sign an artifact-share capability token for a normalized path.

    The path is normalized *before* signing so that ``reports/./x.html``,
    ``reports\\x.html`` and a traversal like ``../secret`` can never be
    smuggled into a valid token: any path that ``normalize_harness_artifact_path``
    rejects is refused here, and the canonical form is what gets signed.

    ``task_frame_id`` is the pre-generalization spelling of ``owner_id`` and is still
    accepted; a Harness TaskFrame owner also gets its id written to ``task_frame_id``
    so the payload keeps its original shape.
    """

    identity = str(owner_id or "").strip() or str(task_frame_id or "").strip()
    if not identity:
        raise ValueError("An artifact share token needs an owner id.")
    kind = normalize_owner_kind(owner_kind)
    now = int(time.time())
    canonical_path = normalize_harness_artifact_path(path)
    ttl = int(ttl_seconds)
    payload = {
        "type": ARTIFACT_SHARE_CLAIM,
        "tenant_id": str(tenant_id).strip(),
        "session_id": str(session_id).strip(),
        "owner_kind": kind,
        "owner_id": identity,
        "path": canonical_path,
        "iat": now,
        "exp": now + ttl,
    }
    if kind == ARTIFACT_OWNER_DEFAULT_KIND:
        payload["task_frame_id"] = identity
    body = _auth._b64(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    return f"{body}.{_auth._sign(body)}"


def decode_artifact_share_token(token: str) -> dict[str, Any]:
    """Validate a share token and return its normalized payload.

    Every failure mode — missing separator, bad signature, unparsable payload,
    wrong claim type, missing claims, malformed owner kind, expired, or an
    enclosing/traversal path — raises HTTP 404. The returned ``path`` is the
    re-normalized canonical form, and ``owner_kind``/``owner_id`` are always present
    (a token minted before ownership was generalized reads its ``task_frame_id`` as a
    Harness TaskFrame owner).
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
    owner_kind, owner_id = _decode_owner_claims(payload)
    if int(payload.get("exp", 0)) < int(time.time()):
        raise _raise_404()
    try:
        canonical = normalize_harness_artifact_path(payload["path"])
    except HarnessArtifactAccessError:
        raise _raise_404() from None
    payload["path"] = canonical
    payload["owner_kind"] = owner_kind
    payload["owner_id"] = owner_id
    return payload


def _decode_owner_claims(payload: dict[str, Any]) -> tuple[str, str]:
    """Read the owner pair, falling back to the legacy ``task_frame_id`` claim.

    Only the two shapes :func:`mint_artifact_share_token` can produce are accepted: a
    complete ``(owner_kind, owner_id)`` pair, or a lone ``task_frame_id`` from before
    ownership was generalized. Anything half-formed fails closed rather than being
    reinterpreted, so a signed token can never resolve as a different owner than the
    one it names. The kind must already be canonical — mint normalizes before signing,
    so no stripping or case folding happens here.
    """

    raw_kind = payload.get("owner_kind")
    raw_identity = payload.get("owner_id")
    if isinstance(raw_kind, str) and raw_kind.strip():
        if not _OWNER_KIND_RE.fullmatch(raw_kind):
            raise _raise_404()
        if not isinstance(raw_identity, str) or not raw_identity.strip():
            raise _raise_404()
        return raw_kind, raw_identity.strip()
    if isinstance(raw_identity, str) and raw_identity.strip():
        # An owner id without a kind is not a shape mint produces.
        raise _raise_404()
    legacy = payload.get("task_frame_id")
    if isinstance(legacy, str) and legacy.strip():
        return ARTIFACT_OWNER_DEFAULT_KIND, legacy.strip()
    raise _raise_404()


def _raise_404() -> HTTPException:
    return HTTPException(status_code=404, detail="Artifact share link not found")


__all__ = [
    "ARTIFACT_SHARE_CLAIM",
    "ARTIFACT_SHARE_TTL_SECONDS",
    "OWNER_KIND_PATTERN",
    "decode_artifact_share_token",
    "mint_artifact_share_token",
    "normalize_owner_kind",
]