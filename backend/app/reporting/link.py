"""Share-link minting for generated reports.

Kept separate from ``writer.py`` so that writing a report and publishing a link to it
are independently callable: the pipeline mints links only when a run finishes
successfully, and a caller may reasonably want the file without a link.

The link is a stateless signed capability (see ``app/security/artifact_share.py``): it
cannot be revoked before it expires, and it must never be written into a scheduled
run's ``trace_json`` — that field is readable through
``GET /api/enterprise/scheduled-tasks/runs`` by anyone who can list runs, which is a
lower bar than reading the session the report belongs to.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from app.config import get_settings
from app.security import artifact_share as artifact_share_mod


@dataclass(frozen=True)
class ReportShareLink:
    """A minted share link plus the facts needed to describe it in a message."""

    url: str
    token: str
    expires_at: int

    @property
    def remaining_seconds(self) -> int:
        return max(0, int(self.expires_at) - int(time.time()))


def mint_report_share_link(
    *,
    tenant_id: str,
    session_id: str,
    owner_kind: str,
    owner_id: str,
    path: str,
    ttl_seconds: int | None = None,
) -> ReportShareLink:
    """Mint a signed share link for a report already written to a workspace.

    This only signs; it does not check that the artifact exists or that the caller may
    read the session. Callers must have resolved the artifact first (the pipeline
    writes it moments earlier; the capability path runs under the requesting user's
    session), which is what ``app.core.artifact_owners`` is for.
    """

    token = artifact_share_mod.mint_artifact_share_token(
        tenant_id=tenant_id,
        session_id=session_id,
        owner_kind=owner_kind,
        owner_id=owner_id,
        path=path,
        ttl_seconds=(
            int(ttl_seconds)
            if ttl_seconds is not None
            else artifact_share_mod.ARTIFACT_SHARE_TTL_SECONDS
        ),
    )
    payload = artifact_share_mod.decode_artifact_share_token(token)
    return ReportShareLink(
        url=f"{get_settings().normalized_tool_base_url}/api/chat/artifacts/view/{token}",
        token=token,
        expires_at=int(payload["exp"]),
    )


__all__ = [
    "ReportShareLink",
    "mint_report_share_link",
]
