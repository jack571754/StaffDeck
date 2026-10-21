import { useMemo } from 'react';
import {
  Table,
  TableBody,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui';
import { Button } from '@/components/ui/button';
import { Download } from 'lucide-react';

export interface ResultTableProps {
  columns: string[];
  rows: Array<Record<string, unknown>>;
  rowCount?: number;
  executionTimeMs?: number;
  cached?: boolean;
  maxHeight?: string;
  showExport?: boolean;
  exportFilename?: string;
  showSummary?: boolean;
}

const NON_SUMMABLE_PATTERN = /(_id$|id$|_code$|code$|_no$|no$|编码|编号|标识|date|time|日期|时间|year|month|day|created|updated|^rk$|^rank$|^seq$|^row_num$|状态|类型|版本|status|version|type|pct|ratio|rate|率|占比)/i;

/**
 * 判断列名是否属于不应简单求和的维度字段（如 ID、编码、日期时间、序号、状态、率等）
 */
export function isNonSummableColumn(colName: string): boolean {
  return NON_SUMMABLE_PATTERN.test(colName.trim());
}

/**
 * 计算多列汇总行数据
 */
export function computeSummaryRow(
  columns: string[],
  rows: Array<Record<string, unknown>>,
): Record<string, unknown> {
  const summary: Record<string, unknown> = {};
  if (!columns.length || !rows.length) return summary;

  let labelPlaced = false;

  columns.forEach((col) => {
    if (isNonSummableColumn(col)) {
      if (!labelPlaced) {
        summary[col] = `合计 (${rows.length}行)`;
        labelPlaced = true;
      } else {
        summary[col] = '—';
      }
      return;
    }

    let hasNumeric = false;
    let sum = 0;
    let hasDecimals = false;
    let validCount = 0;

    for (const row of rows) {
      const val = row[col];
      if (val === null || val === undefined || val === '') continue;
      if (typeof val === 'number') {
        if (!Number.isNaN(val)) {
          hasNumeric = true;
          sum += val;
          validCount++;
          if (val % 1 !== 0) hasDecimals = true;
        }
      } else if (typeof val === 'string' && val.trim() !== '') {
        const num = Number(val);
        if (!Number.isNaN(num)) {
          hasNumeric = true;
          sum += num;
          validCount++;
          if (num % 1 !== 0) hasDecimals = true;
        }
      }
    }

    if (hasNumeric && validCount > 0) {
      const rounded = hasDecimals ? Number(sum.toFixed(4)) : sum;
      summary[col] = rounded;
    } else {
      if (!labelPlaced) {
        summary[col] = `合计 (${rows.length}行)`;
        labelPlaced = true;
      } else {
        summary[col] = '—';
      }
    }
  });

  return summary;
}

/**
 * 格式化汇总数值（千分位、保留适当小数）
 */
export function formatSummaryNumber(val: number): string {
  if (Number.isInteger(val)) {
    return val.toLocaleString('zh-CN');
  }
  const formatted = Number(val.toFixed(4));
  return formatted.toLocaleString('zh-CN', { maximumFractionDigits: 4 });
}

/**
 * 导出 CSV（带 UTF-8 BOM，确保 Excel / WPS 打开中文正常无乱码，并追加汇总行）
 */
export function exportToCsv(
  columns: string[],
  rows: Array<Record<string, unknown>>,
  filename = 'query_results',
  includeSummary = true,
) {
  if (!columns.length || !rows.length) return;
  let csv = '\uFEFF';
  csv += columns.map((col) => `"${String(col).replace(/"/g, '""')}"`).join(',') + '\r\n';
  rows.forEach((row) => {
    const line = columns
      .map((col) => {
        const val = row[col];
        if (val === null || val === undefined) return '""';
        return `"${String(val).replace(/"/g, '""')}"`;
      })
      .join(',');
    csv += line + '\r\n';
  });

  if (includeSummary && rows.length > 0) {
    const summary = computeSummaryRow(columns, rows);
    const summaryLine = columns
      .map((col) => {
        const val = summary[col];
        if (val === null || val === undefined) return '""';
        return `"${String(val).replace(/"/g, '""')}"`;
      })
      .join(',');
    csv += summaryLine + '\r\n';
  }

  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${filename}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

/**
 * 导出 JSON 文件
 */
export function exportToJson(
  rows: Array<Record<string, unknown>>,
  filename = 'query_results',
) {
  if (!rows.length) return;
  const jsonStr = JSON.stringify(rows, null, 2);
  const blob = new Blob([jsonStr], { type: 'application/json;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${filename}.json`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

/**
 * 判断一个值是否为数字类型（number 或可解析为数字的字符串）
 */
function isNumeric(value: unknown): boolean {
  if (typeof value === 'number') return !Number.isNaN(value);
  if (typeof value === 'string' && value.trim() !== '') {
    return !Number.isNaN(Number(value));
  }
  return false;
}

/**
 * 格式化单元格显示值
 */
function formatValue(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

/**
 * 查询结果表格组件
 * 支持横向滚动、数字右对齐、底端粘性汇总行、空数据提示及快捷导出
 */
export default function ResultTable({
  columns,
  rows,
  maxHeight = '400px',
  showExport = false,
  exportFilename = 'query_results',
  showSummary = true,
}: ResultTableProps) {
  const hasData = rows.length > 0 && columns.length > 0;
  const summaryData = useMemo(() => {
    if (!hasData || !showSummary) return {};
    return computeSummaryRow(columns, rows);
  }, [columns, rows, hasData, showSummary]);

  return (
    <div className="flex flex-col gap-[8px]">
      {showExport && hasData && (
        <div className="flex items-center justify-end gap-[6px]">
          <Button
            variant="outline"
            size="xs"
            onClick={() => exportToCsv(columns, rows, exportFilename)}
            className="h-[26px] gap-[4px] px-[8px] text-[11px] text-[#4f566b]"
            title="导出为 CSV 文件 (Excel 兼容)"
          >
            <Download className="size-[12px]" />
            导出 CSV
          </Button>
          <Button
            variant="outline"
            size="xs"
            onClick={() => exportToJson(rows, exportFilename)}
            className="h-[26px] gap-[4px] px-[8px] text-[11px] text-[#4f566b]"
            title="导出为 JSON 文件"
          >
            <Download className="size-[12px]" />
            导出 JSON
          </Button>
        </div>
      )}
      <div
        className="overflow-auto rounded-[10px] border border-[#eceef1]"
        style={{ maxHeight }}
      >
        <Table className="w-max min-w-full table-auto text-[12px]">
          <TableHeader className="sticky top-0 z-10 shadow-xs">
            <TableRow className="border-0 hover:bg-transparent">
              {columns.map((col) => (
                <TableHead
                  key={col}
                  className="h-[36px] whitespace-nowrap border-b border-[#eceef1] bg-[#f2f3f7] px-[12px] py-[8px] text-[12px] font-medium text-[#464c5e]"
                >
                  {col}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {hasData ? (
              rows.map((row, rowIndex) => (
                <TableRow
                  key={rowIndex}
                  className="border-b border-[#f2f3f7] last:border-0 hover:bg-[#fafbfc]"
                >
                  {columns.map((col) => {
                    const value = row[col];
                    const numeric = isNumeric(value);
                    return (
                      <TableCell
                        key={col}
                        className={
                          'whitespace-nowrap px-[12px] py-[8px] text-[12px] text-[#18181a] ' +
                          (numeric ? 'text-right font-mono' : 'text-left')
                        }
                      >
                        {formatValue(value)}
                      </TableCell>
                    );
                  })}
                </TableRow>
              ))
            ) : (
              <TableRow className="hover:bg-transparent">
                <TableCell
                  colSpan={Math.max(columns.length, 1)}
                  className="h-[120px] text-center text-[12px] text-[#858b9c]"
                >
                  暂无数据
                </TableCell>
              </TableRow>
            )}
          </TableBody>
          {hasData && showSummary && (
            <TableFooter className="sticky bottom-0 z-10 border-t-2 border-[#cbd5e1] bg-[#f8fafc] shadow-[0_-2px_6px_rgba(0,0,0,0.03)]">
              <TableRow className="border-0 hover:bg-transparent">
                {columns.map((col) => {
                  const val = summaryData[col];
                  const isNum = typeof val === 'number';
                  return (
                    <TableCell
                      key={col}
                      className={
                        'whitespace-nowrap px-[12px] py-[8px] text-[12px] font-bold ' +
                        (isNum ? 'text-right font-mono text-[#0f172a]' : 'text-left text-[#18181a]')
                      }
                    >
                      {isNum ? formatSummaryNumber(val) : formatValue(val)}
                    </TableCell>
                  );
                })}
              </TableRow>
            </TableFooter>
          )}
        </Table>
      </div>
    </div>
  );
}
