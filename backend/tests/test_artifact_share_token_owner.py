"""Tests for owner claims in signed share tokens (app/security/artifact_share.py).

The token layer must stay dumb about *which* owners exist — that is the resolver's
job — but it must be strict about the shape of the claim it signs and reads. These
tests cover the two directions:

* backwards compatibility, so tokens minted before ownership was generalized still
  decode into a Harness TaskFrame owner;
* strictness, so a malformed or missing owner pair can only ever produce a 404.

Nothing here touches the DB or the filesystem: the token module is pure.
"""

from __future__ import annotations

import base64
import json
import time

import pytest
from fastapi import HTTPException

from app.harness import ARTIFACT_OWNER_DEFAULT_KIND
from app.security import artifact_share as artifact_share_mod
from app.security import auth as security_auth

FRAME_ID = "task_demo"
RUN_ID = "schedrun_demo"
PATH = "reports/report.html"


def _handcraft_token(payload: dict) -> str:
    """Sign an arbitrary payload with the real secret to reach decode checks."""
    body = security_auth._b64(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    return f"{body}.{security_auth._sign(body)}"


def _base_payload(**overrides) -> dict:
    payload = {
        "type": artifact_share_mod.ARTIFACT_SHARE_CLAIM,
        "tenant_id": "tenant_demo",
        "session_id": "session_demo",
        "path": PATH,
        "iat": int(time.time()),
        "exp": int(time.time()) + 600,
    }
    payload.update(overrides)
    return payload


# ------------------------------------------------------- mint / decode round trip


def test_frame_token_round_trips_the_owner_pair() -> None:
    token = artifact_share_mod.mint_artifact_share_token(
        tenant_id="tenant_demo",
        session_id="session_demo",
        task_frame_id=FRAME_ID,
        path=PATH,
    )

    payload = artifact_share_mod.decode_artifact_share_token(token)

    assert payload["owner_kind"] == ARTIFACT_OWNER_DEFAULT_KIND
    assert payload["owner_id"] == FRAME_ID
    # The legacy claim is still written for a frame owner so the payload shape and
    # any reader that predates ownership keep working.
    assert payload["task_frame_id"] == FRAME_ID


def test_scheduled_run_token_round_trips_without_a_task_frame_id() -> None:
    token = artifact_share_mod.mint_artifact_share_token(
        tenant_id="tenant_demo",
        session_id="session_demo",
        owner_kind="scheduled_run",
        owner_id=RUN_ID,
        path=PATH,
    )

    payload = artifact_share_mod.decode_artifact_share_token(token)

    assert payload["owner_kind"] == "scheduled_run"
    assert payload["owner_id"] == RUN_ID
    assert "task_frame_id" not in payload


def test_owner_id_takes_precedence_over_task_frame_id() -> None:
    token = artifact_share_mod.mint_artifact_share_token(
        tenant_id="tenant_demo",
        session_id="session_demo",
        owner_kind="scheduled_run",
        owner_id=RUN_ID,
        task_frame_id=FRAME_ID,
        path=PATH,
    )

    payload = artifact_share_mod.decode_artifact_share_token(token)

    assert payload["owner_id"] == RUN_ID


def test_minting_without_any_owner_id_is_rejected() -> None:
    with pytest.raises(ValueError):
        artifact_share_mod.mint_artifact_share_token(
            tenant_id="tenant_demo",
            session_id="session_demo",
            path=PATH,
        )


def test_minting_a_malformed_owner_kind_is_rejected() -> None:
    with pytest.raises(ValueError):
        artifact_share_mod.mint_artifact_share_token(
            tenant_id="tenant_demo",
            session_id="session_demo",
            owner_kind="Not A Slug",
            owner_id=RUN_ID,
            path=PATH,
        )


def test_path_is_normalized_before_signing() -> None:
    token = artifact_share_mod.mint_artifact_share_token(
        tenant_id="tenant_demo",
        session_id="session_demo",
        task_frame_id=FRAME_ID,
        path="reports\\sub/./report.html",
    )

    assert artifact_share_mod.decode_artifact_share_token(token)["path"] == (
        "reports/sub/report.html"
    )


# ------------------------------------------------------- legacy fallback


def test_legacy_token_without_owner_claims_decodes_as_a_frame_owner() -> None:
    token = _handcraft_token(_base_payload(task_frame_id=FRAME_ID))

    payload = artifact_share_mod.decode_artifact_share_token(token)

    assert payload["owner_kind"] == ARTIFACT_OWNER_DEFAULT_KIND
    assert payload["owner_id"] == FRAME_ID


def test_token_without_any_ownership_claim_is_rejected() -> None:
    token = _handcraft_token(_base_payload())

    with pytest.raises(HTTPException) as rejected:
        artifact_share_mod.decode_artifact_share_token(token)

    assert rejected.value.status_code == 404


def test_owner_kind_without_an_id_is_rejected_even_with_a_legacy_claim() -> None:
    # A kind with no id is not a shape mint produces. It must not be quietly
    # reinterpreted as the legacy frame owner the payload also names.
    token = _handcraft_token(
        _base_payload(owner_kind="scheduled_run", task_frame_id=FRAME_ID)
    )

    with pytest.raises(HTTPException) as rejected:
        artifact_share_mod.decode_artifact_share_token(token)

    assert rejected.value.status_code == 404


def test_owner_id_without_a_kind_is_rejected() -> None:
    # Half a pair is not a shape mint produces, and there is no legacy claim to fall
    # back on, so it fails closed instead of being reinterpreted.
    token = _handcraft_token(_base_payload(owner_id=FRAME_ID))

    with pytest.raises(HTTPException) as rejected:
        artifact_share_mod.decode_artifact_share_token(token)

    assert rejected.value.status_code == 404


# ------------------------------------------------------- strictness


@pytest.mark.parametrize(
    "bad_kind",
    [
        "Scheduled_Run",  # uppercase
        "1leading_digit",
        "has-dash",
        "has space",
        "has/slash",
        "",  # empty
        "x" * 33,  # longer than 32 characters
        "scheduled_run\n",
    ],
)
def test_malformed_owner_kind_in_a_signed_token_is_rejected(bad_kind: str) -> None:
    token = _handcraft_token(
        _base_payload(owner_kind=bad_kind, owner_id=RUN_ID, task_frame_id=FRAME_ID)
    )

    with pytest.raises(HTTPException) as rejected:
        artifact_share_mod.decode_artifact_share_token(token)

    assert rejected.value.status_code == 404


def test_blank_owner_id_in_a_signed_token_is_rejected() -> None:
    token = _handcraft_token(
        _base_payload(owner_kind="scheduled_run", owner_id="   ", task_frame_id=FRAME_ID)
    )

    with pytest.raises(HTTPException) as rejected:
        artifact_share_mod.decode_artifact_share_token(token)

    assert rejected.value.status_code == 404


def test_tampered_owner_kind_breaks_the_signature() -> None:
    token = artifact_share_mod.mint_artifact_share_token(
        tenant_id="tenant_demo",
        session_id="session_demo",
        owner_kind="scheduled_run",
        owner_id=RUN_ID,
        path=PATH,
    )
    body, signature = token.split(".", 1)
    payload = json.loads(base64.urlsafe_b64decode(security_auth._pad_b64(body)))
    payload["owner_kind"] = ARTIFACT_OWNER_DEFAULT_KIND
    forged_body = security_auth._b64(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )

    with pytest.raises(HTTPException) as rejected:
        artifact_share_mod.decode_artifact_share_token(f"{forged_body}.{signature}")

    assert rejected.value.status_code == 404


def test_expired_token_is_rejected_regardless_of_ownership() -> None:
    token = _handcraft_token(
        _base_payload(
            owner_kind="scheduled_run",
            owner_id=RUN_ID,
            exp=int(time.time()) - 1,
        )
    )

    with pytest.raises(HTTPException) as rejected:
        artifact_share_mod.decode_artifact_share_token(token)

    assert rejected.value.status_code == 404


def test_traversal_path_is_rejected_regardless_of_ownership() -> None:
    token = _handcraft_token(
        _base_payload(
            owner_kind="scheduled_run",
            owner_id=RUN_ID,
            path="../../secret.html",
        )
    )

    with pytest.raises(HTTPException) as rejected:
        artifact_share_mod.decode_artifact_share_token(token)

    assert rejected.value.status_code == 404


def test_normalize_owner_kind_defaults_and_lowercases() -> None:
    assert artifact_share_mod.normalize_owner_kind(None) == ARTIFACT_OWNER_DEFAULT_KIND
    assert artifact_share_mod.normalize_owner_kind("") == ARTIFACT_OWNER_DEFAULT_KIND
    assert artifact_share_mod.normalize_owner_kind("  Scheduled_Run ") == "scheduled_run"
