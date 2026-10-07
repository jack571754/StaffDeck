"""Shareable HTML report generation.

Turns query results into a self-contained single-file HTML report that can be
previewed in chat, shared as a signed short link, and linked from a Feishu card.

Layout:

* ``document.py``  — the pure data contract (no IO, no DB, no HTML)
* ``format.py``    — neutral value formatters (no Feishu colour markup)
* ``assets.py``    — the inlined CSS/JS constants
* ``html.py``      — the single serializer and the single escaping exit
* ``renderers/``   — rows -> ReportDocument, registered in ``REPORT_RENDERERS``
* ``writer.py``    — the only module that touches the DB and the filesystem
* ``link.py``      — signed share-link minting for a written report

The "share" half of this feature already exists in ``app/security/artifact_share.py``
and ``app/api/chat.py``; this package only adds the "generate" half.
"""

from __future__ import annotations

from app.reporting.document import (
    GENERATOR_ID,
    MAX_BLOCKS,
    MAX_COLUMNS,
    MAX_DATASET_ROWS,
    MAX_DATASETS,
    KpiItem,
    ReportBlock,
    ReportDataset,
    ReportDocument,
    ReportMeta,
    json_safe,
    validate_document,
)
from app.reporting.errors import ReportDocumentError, ReportError, ReportRenderError
from app.reporting.format import (
    format_cell,
    format_metric_delta,
    format_metric_value,
    to_float,
)
from app.reporting.html import build_island, render_report_html
from app.reporting.link import ReportShareLink, mint_report_share_link
from app.reporting.renderers import (
    DEFAULT_RENDERER_NAME,
    REPORT_RENDERERS,
    ReportRenderer,
    build_generic_table_report,
    build_sales_report,
    get_report_renderer,
    report_renderer_names,
)
from app.reporting.spec import SPEC_KEYS, build_report_from_spec
from app.reporting.writer import (
    MAX_REPORT_BYTES,
    REPORT_CONTENT_TYPE,
    PublishedReport,
    report_file_name,
    write_report_html,
)

__all__ = [
    "DEFAULT_RENDERER_NAME",
    "GENERATOR_ID",
    "MAX_BLOCKS",
    "MAX_COLUMNS",
    "MAX_DATASETS",
    "MAX_DATASET_ROWS",
    "MAX_REPORT_BYTES",
    "REPORT_CONTENT_TYPE",
    "REPORT_RENDERERS",
    "SPEC_KEYS",
    "KpiItem",
    "PublishedReport",
    "ReportBlock",
    "ReportDataset",
    "ReportDocument",
    "ReportDocumentError",
    "ReportError",
    "ReportMeta",
    "ReportRenderError",
    "ReportRenderer",
    "ReportShareLink",
    "build_generic_table_report",
    "build_island",
    "build_report_from_spec",
    "build_sales_report",
    "format_cell",
    "format_metric_delta",
    "format_metric_value",
    "get_report_renderer",
    "json_safe",
    "mint_report_share_link",
    "render_report_html",
    "report_file_name",
    "report_renderer_names",
    "to_float",
    "validate_document",
    "write_report_html",
]
