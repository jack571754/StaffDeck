import { useMemo, useState } from 'react';
import {
  Badge,
  Button,
  Input,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Table,
  TableBody,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui';
import type { QueryExecuteResult } from '@/api/data-query';
import { exportToCsv, exportToJson, computeSummaryRow, formatSummaryNumber } from './ResultTable';
import {
  Download,
  Search,
  Loader2,
  AlertCircle,
  Database,
  Clock,
  Layers,
  ChevronLeft,
  ChevronRight,
  Maximize2,
  Minimize2,
  X,
  FileSpreadsheet,
  FileJson,
} from 'lucide-react';

const CARD_CLASS = 'rounded-[14px] border border-[#eceef1] bg-white';
const CARD_TITLE_CLASS = 'text-[14px] font-medium text-[#18181a]';

export interface TestResultSectionProps {
  result: QueryExecuteResult | null;
  loading: boolean;
  error: string | null;
  templateName?: string;
  dataSourceName?: string;
  onClear?: () => void;
}

function isNumeric(value: unknown): boolean {
  if (typeof value === 'number') return !Number.isNaN(value);
  if (typeof value === 'string' && value.trim() !== '') {
    return !Number.isNaN(Number(value));
  }
  return false;
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

export default function TestResultSection({
  result,
  loading,
  error,
  templateName,
  dataSourceName,
}: TestResultSectionProps) {
  const [searchTerm, setSearchTerm] = useState('');
  const [pageSize, setPageSize] = useState<number>(25);
  const [currentPage, setCurrentPage] = useState<number>(1);
  const [isExpanded, setIsExpanded] = useState<boolean>(false);

  // 搜索过滤数据行
  const filteredRows = useMemo(() => {
    if (!result?.rows) return [];
    const term = searchTerm.trim().toLowerCase();
    if (!term) return result.rows;
    return result.rows.filter((row) =>
      Object.values(row).some((val) => {
        if (val === null || val === undefined) return false;
        return String(val).toLowerCase().includes(term);
      }),
    );
  }, [result?.rows, searchTerm]);

  // 当搜索条件或结果变化时，重置分页到第一页
  useMemo(() => {
    setCurrentPage(1);
  }, [searchTerm, result]);

  const totalRows = filteredRows.length;
  const isAllPages = pageSize === -1;
  const totalPages = isAllPages ? 1 : Math.max(1, Math.ceil(totalRows / pageSize));
  const safeCurrentPage = Math.min(currentPage, totalPages);

  const displayedRows = useMemo(() => {
    if (isAllPages) return filteredRows;
    const start = (safeCurrentPage - 1) * pageSize;
    return filteredRows.slice(start, start + pageSize);
  }, [filteredRows, isAllPages, safeCurrentPage, pageSize]);

  const filename = `${templateName ? templateName.replace(/\s+/g, '_') : 'query_test'}_${new Date().toISOString().slice(0, 10)}`;

  const summaryData = useMemo(() => {
    if (!result?.columns || !filteredRows.length) return {};
    return computeSummaryRow(result.columns, filteredRows);
  }, [result?.columns, filteredRows]);

  const handleExportCsv = () => {
    if (!result?.columns || !filteredRows.length) return;
    exportToCsv(result.columns, filteredRows, filename);
  };

  const handleExportJson = () => {
    if (!filteredRows.length) return;
    exportToJson(filteredRows, filename);
  };

  const tableMaxHeight = isExpanded ? '750px' : '450px';

  return (
    <section className={CARD_CLASS + ' overflow-hidden shadow-sm'} data-testid="test-result-section">
      {/* 头部标题与操作栏 */}
      <div className="flex flex-wrap items-center justify-between gap-[12px] border-b border-[#eceef1] bg-[#fafbfc] px-[20px] py-[12px]">
        <div className="flex flex-wrap items-center gap-[10px]">
          <div className="flex items-center gap-[8px]">
            <Database className="size-[16px] text-[#4f566b]" />
            <span className={CARD_TITLE_CLASS}>测试运行结果展示</span>
          </div>

          {/* 状态指示徽章 */}
          {result && (
            <div className="flex flex-wrap items-center gap-[6px]">
              <Badge variant="outline" className="border-[#e2e4e9] bg-white text-[#464c5e]">
                <Layers className="mr-1 size-3" />
                共 {result.row_count} 行 · {result.columns.length} 字段
              </Badge>
              <Badge variant="outline" className="border-[#e2e4e9] bg-white text-[#464c5e]">
                <Clock className="mr-1 size-3" />
                耗时 {result.execution_time_ms} ms
              </Badge>
              {result.cached && (
                <Badge variant="outline" className="border-[#bbf7d0] bg-[#f0fdf4] text-[#16a34a]">
                  缓存命中
                </Badge>
              )}
              {dataSourceName && (
                <Badge variant="outline" className="border-[#e2e4e9] bg-white text-[#6b7280]">
                  源: {dataSourceName}
                </Badge>
              )}
            </div>
          )}
        </div>

        {/* 右侧工具栏：搜索、导出与全屏切换 */}
        <div className="flex flex-wrap items-center gap-[8px]">
          {result && result.rows.length > 0 && (
            <>
              {/* 搜索框 */}
              <div className="relative w-[180px] sm:w-[220px]">
                <Search className="pointer-events-none absolute left-[8px] top-1/2 size-[13px] -translate-y-1/2 text-[#858b9c]" />
                <Input
                  type="text"
                  placeholder="搜索结果数据..."
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                  className="h-[28px] pl-[26px] pr-[24px] text-[12px]"
                />
                {searchTerm && (
                  <button
                    type="button"
                    onClick={() => setSearchTerm('')}
                    className="absolute right-[6px] top-1/2 -translate-y-1/2 text-[#858b9c] hover:text-[#18181a]"
                  >
                    <X className="size-[12px]" />
                  </button>
                )}
              </div>

              {/* 导出按钮 */}
              <Button
                variant="outline"
                size="xs"
                onClick={handleExportCsv}
                className="h-[28px] gap-[4px] px-[8px] text-[12px] text-[#4f566b] hover:bg-white hover:text-[#18181a]"
                title="导出为 CSV 表格 (支持 Excel / WPS 直接打开，含 UTF-8 BOM 避免乱码)"
              >
                <FileSpreadsheet className="size-[13px] text-[#16a34a]" />
                导出 CSV
              </Button>

              <Button
                variant="outline"
                size="xs"
                onClick={handleExportJson}
                className="h-[28px] gap-[4px] px-[8px] text-[12px] text-[#4f566b] hover:bg-white hover:text-[#18181a]"
                title="导出为标准 JSON 格式"
              >
                <FileJson className="size-[13px] text-[#2563eb]" />
                导出 JSON
              </Button>

              {/* 高度展开/收起切换 */}
              <Button
                variant="ghost"
                size="xs"
                onClick={() => setIsExpanded(!isExpanded)}
                className="h-[28px] px-[6px] text-[#858b9c] hover:text-[#18181a]"
                title={isExpanded ? '恢复默认高度' : '扩展表格查看区域'}
              >
                {isExpanded ? <Minimize2 className="size-[14px]" /> : <Maximize2 className="size-[14px]" />}
              </Button>
            </>
          )}
        </div>
      </div>

      {/* 主体内容 */}
      <div className="p-[16px]">
        {/* 1. 运行中状态 */}
        {loading && (
          <div className="flex flex-col items-center justify-center py-[48px] text-[#858b9c]">
            <Loader2 className="mb-[10px] size-[28px] animate-spin text-[#2563eb]" />
            <div className="text-[14px] font-medium text-[#18181a]">正在执行查询...</div>
            <div className="mt-[4px] text-[12px] text-[#858b9c]">
              已向后端数据源发起执行请求，正在拉取并解析结果，请稍候
            </div>
          </div>
        )}

        {/* 2. 报错状态 */}
        {!loading && error && (
          <div className="rounded-[10px] border border-[#fecaca] bg-[#fef2f2] p-[16px]">
            <div className="flex items-center gap-[8px] text-[13px] font-medium text-[#b91c1c]">
              <AlertCircle className="size-[16px] shrink-0" />
              <span>查询执行失败</span>
            </div>
            <pre className="mt-[8px] max-h-[160px] overflow-auto rounded-[6px] bg-white/70 p-[10px] font-mono text-[11px] text-[#dc2626]">
              {error}
            </pre>
            <div className="mt-[8px] text-[11px] text-[#7f1d1d]">
              提示：请检查 SQL 语法、参数名与参数值是否合法，或在「数据链接管理」中检查数据源连通性。
            </div>
          </div>
        )}

        {/* 3. 尚未运行状态 */}
        {!loading && !error && !result && (
          <div className="flex flex-col items-center justify-center py-[44px] text-[#858b9c]">
            <div className="flex size-[48px] items-center justify-center rounded-full bg-[#f4f5f8] text-[#858b9c]">
              <Database className="size-[24px]" />
            </div>
            <div className="mt-[12px] text-[13px] font-medium text-[#464c5e]">暂无测试运行结果</div>
            <div className="mt-[4px] max-w-[460px] text-center text-[12px] text-[#858b9c]">
              在右侧面板填入测试参数后点击「运行测试」，查询结果与字段明细将在此处全宽展示，支持即时搜索、分页与快捷导出。
            </div>
          </div>
        )}

        {/* 4. 执行成功但 0 行 */}
        {!loading && !error && result && result.rows.length === 0 && (
          <div className="rounded-[10px] border border-[#eceef1] bg-[#fcfdfe] py-[36px] text-center text-[13px] text-[#858b9c]">
            查询成功执行并返回，但未匹配到任何数据行（共 0 行）。
          </div>
        )}

        {/* 5. 执行成功且有数据 */}
        {!loading && !error && result && result.rows.length > 0 && (
          <div className="flex flex-col gap-[12px]">
            {/* 搜索无结果时的提示 */}
            {filteredRows.length === 0 ? (
              <div className="rounded-[10px] border border-[#eceef1] bg-[#fafbfc] py-[32px] text-center text-[12px] text-[#858b9c]">
                未找到包含「<span className="font-semibold text-[#18181a]">{searchTerm}</span>」的记录，
                <button
                  type="button"
                  onClick={() => setSearchTerm('')}
                  className="ml-1 text-[#2563eb] underline hover:text-[#1d4ed8]"
                >
                  清空搜索条件
                </button>
              </div>
            ) : (
              <>
                {/* 结果表格 */}
                <div
                  className="overflow-auto rounded-[10px] border border-[#eceef1]"
                  style={{ maxHeight: tableMaxHeight }}
                >
                  <Table className="w-max min-w-full table-auto text-[12px]">
                    <TableHeader className="sticky top-0 z-10 shadow-sm">
                      <TableRow className="border-0 hover:bg-transparent">
                        <TableHead className="w-[48px] whitespace-nowrap border-b border-[#eceef1] bg-[#f2f3f7] px-[10px] py-[8px] text-center text-[11px] font-medium text-[#64748b]">
                          #
                        </TableHead>
                        {result.columns.map((col) => (
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
                      {displayedRows.map((row, idx) => {
                        const rowIndex = isAllPages ? idx + 1 : (safeCurrentPage - 1) * pageSize + idx + 1;
                        return (
                          <TableRow
                            key={idx}
                            className="border-b border-[#f2f3f7] last:border-0 hover:bg-[#f8fafc]"
                          >
                            <TableCell className="whitespace-nowrap px-[10px] py-[8px] text-center font-mono text-[11px] text-[#94a3b8]">
                              {rowIndex}
                            </TableCell>
                            {result.columns.map((col) => {
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
                        );
                      })}
                    </TableBody>
                    {filteredRows.length > 0 && (
                      <TableFooter className="sticky bottom-0 z-10 border-t-2 border-[#cbd5e1] bg-[#f8fafc] shadow-[0_-2px_6px_rgba(0,0,0,0.04)]">
                        <TableRow className="border-0 hover:bg-transparent">
                          <TableCell className="whitespace-nowrap px-[10px] py-[8px] text-center font-bold text-[#475569]">
                            ∑
                          </TableCell>
                          {result.columns.map((col) => {
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

                {/* 分页控制栏 */}
                <div className="flex flex-wrap items-center justify-between gap-[12px] text-[12px] text-[#6b7280]">
                  <div className="flex items-center gap-[6px]">
                    <span>
                      显示第{' '}
                      <span className="font-medium text-[#18181a]">
                        {isAllPages ? 1 : (safeCurrentPage - 1) * pageSize + 1}
                      </span>
                      -
                      <span className="font-medium text-[#18181a]">
                        {isAllPages ? totalRows : Math.min(safeCurrentPage * pageSize, totalRows)}
                      </span>{' '}
                      行，共 <span className="font-medium text-[#18181a]">{totalRows}</span> 行
                      {searchTerm && (
                        <span className="ml-1 text-[#858b9c]">
                          （从原始 {result.row_count} 行中筛选）
                        </span>
                      )}
                    </span>
                  </div>

                  <div className="flex items-center gap-[12px]">
                    {/* 每页数量选择 */}
                    <div className="flex items-center gap-[6px]">
                      <span className="text-[11px] text-[#858b9c]">每页:</span>
                      <Select
                        value={String(pageSize)}
                        onValueChange={(val) => {
                          setPageSize(Number(val));
                          setCurrentPage(1);
                        }}
                      >
                        <SelectTrigger className="h-[26px] w-[76px] px-[8px] text-[11px]">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="10">10 行</SelectItem>
                          <SelectItem value="25">25 行</SelectItem>
                          <SelectItem value="50">50 行</SelectItem>
                          <SelectItem value="100">100 行</SelectItem>
                          <SelectItem value="-1">全部</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>

                    {/* 翻页按钮 */}
                    {!isAllPages && totalPages > 1 && (
                      <div className="flex items-center gap-[4px]">
                        <Button
                          variant="outline"
                          size="xs"
                          disabled={safeCurrentPage <= 1}
                          onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                          className="h-[26px] px-[6px]"
                        >
                          <ChevronLeft className="size-[13px]" />
                        </Button>
                        <span className="px-[6px] text-[11px] font-medium text-[#18181a]">
                          {safeCurrentPage} / {totalPages}
                        </span>
                        <Button
                          variant="outline"
                          size="xs"
                          disabled={safeCurrentPage >= totalPages}
                          onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
                          className="h-[26px] px-[6px]"
                        >
                          <ChevronRight className="size-[13px]" />
                        </Button>
                      </div>
                    )}
                  </div>
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
