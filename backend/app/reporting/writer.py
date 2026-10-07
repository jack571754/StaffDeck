"""The only reporting module that touches the database and the filesystem.

Everything above this layer (``document``/``html``/``renderers``) is a pure function.
This module decides *where* a report lands, writes it atomically, and turns it into a
published artifact manifest entry so the existing download/share endpoints can serve
it without any new route.

Layout is delegated to ``core.harness_session_cleanup`` so the writer and the resolver
in ``core.artifact_owners`` can never disagree about where a report lives. That
matters twice over: the report's relative path must be relative to exactly the
workspace root the resolver hands to ``open_harness_artifact``, and those helpers also
reject symlinked path components — without which a write could succeed and the
read-back would then be refused.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from sqlmodel import Session

from app.core.harness_session_cleanup import (
    harness_owner_workspace_root,
    harness_path_segment,
    harness_reports_root,
)
from app.db.models import new_id
from app.harness import ARTIFACT_OWNER_DEFAULT_KIND, publish_harness_artifacts
from app.reporting.document import ReportDocument
from app.reporting.errors import ReportRenderError
from app.reporting.html import render_report_html

# Hard ceiling on one report file. `publish_harness_artifacts` defaults to 50 MiB; a
# report is a snapshot, so anything approaching that is a mistake worth failing on.
MAX_REPORT_BYTES = 20 * 1024 * 1024
# Keep the publish-time limit aligned with this module's own ceiling instead of
# silently inheriting the larger default.
_PUBLISH_MAX_FILE_BYTES = 25 * 1024 * 1024
# Temp files are written as their own path segment beginning with ".tmp-" so both the
# Harness user-facing-file filter and artifact auto-discovery skip them.
_TEMP_PREFIX = ".tmp-"
_REPORT_OPERATION = "report_generation"
REPORT_CONTENT_TYPE = "text/html; charset=utf-8"


@dataclass(frozen=True)
class PublishedReport:
    """A report written to disk and registered as a published artifact."""

    path: str
    sha256: str
    size: int
    display_name: str
    owner_kind: str
    owner_id: str
    artifact: dict[str, Any]


def report_file_name(report_id: str) -> str:
    """Build a stable, filesystem-safe file name for one report."""

    return f"{harness_path_segment(report_id)}.html"


def write_report_html(
    *,
    db: Session,
    tenant_id: str,
    session_id: str,
    owner_kind: str,
    owner_id: str,
    document: ReportDocument,
    report_id: str | None = None,
    file_name: str | None = None,
) -> PublishedReport:
    """Render, persist and publish one HTML report.

    Raises ``ReportRenderError`` when the document cannot be rendered or the rendered
    file exceeds :data:`MAX_REPORT_BYTES`; callers turn that into a failed step or a
    failed capability call rather than a 500.
    """

    identity = str(report_id or "").strip() or new_id("report")
    name = file_name or report_file_name(identity)
    workspace_root = harness_owner_workspace_root(
        tenant_id=tenant_id,
        session_id=session_id,
        owner_kind=owner_kind,
        owner_id=owner_id,
        db=db,
    )
    reports_root = harness_reports_root(
        tenant_id=tenant_id,
        session_id=session_id,
        owner_kind=owner_kind,
        owner_id=owner_id,
        db=db,
    )
    # Artifact paths are workspace-relative, because the workspace root is what the
    # resolver passes to open_harness_artifact.
    relative_path = (reports_root / name).relative_to(workspace_root).as_posix()

    encoded = render_report_html(document).encode("utf-8")
    if len(encoded) > MAX_REPORT_BYTES:
        raise ReportRenderError(
            f"报告体积 {len(encoded) // (1024 * 1024)} MiB 超过上限 "
            f"{MAX_REPORT_BYTES // (1024 * 1024)} MiB，请减少行数或列数后重试。"
        )

    reports_root.mkdir(parents=True, exist_ok=True)
    temporary = reports_root / f"{_TEMP_PREFIX}{identity}.html"
    try:
        with open(temporary, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        # os.replace is atomic within one filesystem, so a reader sees either the
        # previous report or this one, never a partial file.
        os.replace(temporary, reports_root / name)
    except OSError as exc:
        raise ReportRenderError(f"报告写入失败：{exc}") from exc
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)

    published = publish_harness_artifacts(
        workspace_root,
        owner_id,
        [{"path": relative_path}],
        operation=_REPORT_OPERATION,
        max_file_bytes=_PUBLISH_MAX_FILE_BYTES,
    )
    if not published:
        raise ReportRenderError("报告已写入但未能登记为交付物。")

    artifact: dict[str, Any] = dict(published[0])
    display_name = name if file_name else f"{_title_of(document)}.html"
    artifact["display_name"] = display_name
    artifact["content_type"] = REPORT_CONTENT_TYPE
    artifact["description"] = "可分享的 HTML 报告"
    # Ownership beyond the legacy `task_frame_id` field, so the resolver can find this
    # artifact for an owner that is not a TaskFrame (e.g. a scheduled pipeline run).
    artifact["owner_kind"] = str(owner_kind or ARTIFACT_OWNER_DEFAULT_KIND)
    artifact["owner_id"] = str(owner_id)
    return PublishedReport(
        path=relative_path,
        sha256=str(artifact.get("sha256") or ""),
        size=int(artifact.get("size") or 0),
        display_name=display_name,
        owner_kind=str(artifact["owner_kind"]),
        owner_id=str(owner_id),
        artifact=artifact,
    )


def _title_of(document: ReportDocument) -> str:
    return str(document.meta.title or "").strip() or "报告"


__all__ = [
    "MAX_REPORT_BYTES",
    "REPORT_CONTENT_TYPE",
    "PublishedReport",
    "report_file_name",
    "write_report_html",
]
