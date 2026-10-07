"""Exceptions raised by the reporting module."""

from __future__ import annotations


class ReportError(Exception):
    """Base class for reporting failures."""


class ReportDocumentError(ReportError, ValueError):
    """The ReportDocument is structurally invalid (unknown block kind, dangling dataset, ...)."""


class ReportRenderError(ReportError):
    """Rendering or persisting the report failed (size guard, IO error, ...)."""
