import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui';

export interface ResultTableProps {
  columns: string[];
  rows: Array<Record<string, unknown>>;
  rowCount?: number;
  executionTimeMs?: number;
  cached?: boolean;
  maxHeight?: string;
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
 * 支持横向滚动、数字右对齐、空数据提示
 */
export default function ResultTable({
  columns,
  rows,
  maxHeight = '400px',
}: ResultTableProps) {
  const hasData = rows.length > 0 && columns.length > 0;

  return (
    <div
      className="overflow-auto rounded-[10px] border border-[#eceef1]"
      style={{ maxHeight }}
    >
      <Table className="w-max min-w-full table-auto text-[12px]">
        <TableHeader className="sticky top-0 z-10">
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
      </Table>
    </div>
  );
}
