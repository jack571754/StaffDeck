"""Pure data contract for shareable HTML reports.

No IO, no database, no HTML. Everything here is a frozen dataclass so a document
can be built in one place and serialized (or unit-tested) in another.

The document is deliberately "dumb": every human-readable string (KPI values,
deltas, table cells) is pre-formatted by the caller/renderer, so the HTML layer
never has to know about units or business semantics.
"""

from __future__ import annotations

import base64
import math
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Literal

from app.reporting.errors import ReportDocumentError

BlockKind = Literal[
    "heading",
    "markdown",
    "kpi_row",
    "table",
    "bar_chart",
    "line_chart",
    "divider",
    "note",
]
DeltaTone = Literal["up", "down", "flat"]

GENERATOR_ID = "staffdeck.reporting/1"

# Size guards. Query results can be arbitrarily large and the data island/table is
# what actually dominates the payload, so these are hard limits rather than hints.
MAX_DATASETS = 8
MAX_BLOCKS = 40
MAX_DATASET_ROWS = 500
MAX_COLUMNS = 40

BLOCK_KINDS: frozenset[str] = frozenset(
    {"heading", "markdown", "kpi_row", "table", "bar_chart", "line_chart", "divider", "note"}
)
_DATASET_BACKED_KINDS: frozenset[str] = frozenset({"table", "bar_chart", "line_chart"})
_CHART_KINDS: frozenset[str] = frozenset({"bar_chart", "line_chart"})
_DELTA_TONES: frozenset[str] = frozenset({"up", "down", "flat"})


def json_safe(value: Any) -> Any:
    """Coerce a value into something ``json.dumps`` accepts.

    This is required, not defensive: rows coming out of pymysql may contain
    ``Decimal`` (json.dumps raises TypeError), ``datetime``/``date``, and ``bytes``.
    Non-finite floats are stringified because ``json.dumps`` emits bare ``NaN``,
    which is not valid JSON.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Decimal):
        try:
            return float(value)
        except (TypeError, ValueError, OverflowError):
            return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        # Sorted so output stays deterministic (golden tests, stable diffs).
        return [json_safe(v) for v in sorted(value, key=repr)]
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return str(value)


@dataclass(frozen=True)
class ReportMeta:
    title: str
    generated_at: datetime
    tenant_id: str = ""
    subtitle: str = ""
    generator: str = GENERATOR_ID
    source: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReportDataset:
    """A named table of rows referenced by blocks via ``dataset_id``."""

    id: str
    columns: list[str]
    rows: list[dict[str, Any]]
    # Display labels keyed by column name. Lets two columns share a label
    # (e.g. three "环比净销" columns) while keeping distinct dict keys.
    column_labels: dict[str, str] = field(default_factory=dict)
    source: dict[str, Any] = field(default_factory=dict)

    def label_for(self, column: str) -> str:
        return self.column_labels.get(column, column)


@dataclass(frozen=True)
class KpiItem:
    label: str
    value: str
    delta: str | None = None
    delta_tone: DeltaTone | None = None
    hint: str | None = None


@dataclass(frozen=True)
class ReportBlock:
    kind: BlockKind
    text: str | None = None
    level: int | None = None
    dataset_id: str | None = None
    columns: list[str] | None = None
    kpis: list[KpiItem] | None = None
    x: str | None = None
    y: list[str] | None = None
    sortable: bool = True
    filterable: bool = True
    max_rows: int | None = None


@dataclass(frozen=True)
class ReportDocument:
    meta: ReportMeta
    datasets: list[ReportDataset] = field(default_factory=list)
    blocks: list[ReportBlock] = field(default_factory=list)


def validate_document(document: ReportDocument) -> None:
    """Raise ``ReportDocumentError`` when the document cannot be rendered safely."""
    if not document.meta.title.strip():
        raise ReportDocumentError("报告标题不能为空。")
    if len(document.datasets) > MAX_DATASETS:
        raise ReportDocumentError(
            f"数据集数量 {len(document.datasets)} 超出上限 {MAX_DATASETS}。"
        )
    if len(document.blocks) > MAX_BLOCKS:
        raise ReportDocumentError(f"内容块数量 {len(document.blocks)} 超出上限 {MAX_BLOCKS}。")

    seen_ids: set[str] = set()
    for dataset in document.datasets:
        if not dataset.id:
            raise ReportDocumentError("数据集 id 不能为空。")
        if dataset.id in seen_ids:
            raise ReportDocumentError(f"数据集 id 重复：{dataset.id}")
        seen_ids.add(dataset.id)
        if len(dataset.columns) > MAX_COLUMNS:
            raise ReportDocumentError(
                f"数据集 {dataset.id} 列数 {len(dataset.columns)} 超出上限 {MAX_COLUMNS}。"
            )

    for index, block in enumerate(document.blocks, 1):
        if block.kind not in BLOCK_KINDS:
            raise ReportDocumentError(f"内容块 {index} 使用了未知类型：{block.kind!r}")
        for kpi in block.kpis or []:
            if kpi.delta_tone is not None and kpi.delta_tone not in _DELTA_TONES:
                raise ReportDocumentError(f"内容块 {index} 的 KPI 语气值非法：{kpi.delta_tone!r}")

        if block.kind not in _DATASET_BACKED_KINDS:
            continue
        if not block.dataset_id:
            raise ReportDocumentError(f"内容块 {index}（{block.kind}）缺少 dataset_id。")
        dataset = next((d for d in document.datasets if d.id == block.dataset_id), None)
        if dataset is None:
            raise ReportDocumentError(
                f"内容块 {index}（{block.kind}）引用了不存在的数据集：{block.dataset_id}"
            )
        for column in block.columns or []:
            if column not in dataset.columns:
                raise ReportDocumentError(
                    f"内容块 {index} 引用了数据集 {dataset.id} 中不存在的列：{column}"
                )
        if block.kind in _CHART_KINDS:
            if not block.x:
                raise ReportDocumentError(f"内容块 {index}（{block.kind}）缺少 x 轴列。")
            if block.x not in dataset.columns:
                raise ReportDocumentError(
                    f"内容块 {index} 的 x 轴列 {block.x} 不在数据集 {dataset.id} 中。"
                )
            if not block.y:
                raise ReportDocumentError(f"内容块 {index}（{block.kind}）缺少 y 轴系列列。")
            for column in block.y:
                if column not in dataset.columns:
                    raise ReportDocumentError(
                        f"内容块 {index} 的 y 轴列 {column} 不在数据集 {dataset.id} 中。"
                    )


def iter_referenced_datasets(document: ReportDocument) -> list[ReportDataset]:
    """Datasets in the order they are first referenced by blocks."""
    by_id = {dataset.id: dataset for dataset in document.datasets}
    ordered: list[ReportDataset] = []
    seen: set[str] = set()
    for block in document.blocks:
        if block.dataset_id and block.dataset_id not in seen and block.dataset_id in by_id:
            seen.add(block.dataset_id)
            ordered.append(by_id[block.dataset_id])
    return ordered
