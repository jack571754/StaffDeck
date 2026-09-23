import { useEffect, useState, useMemo, useCallback } from 'react';
import {
  ChevronDown,
  ChevronRight,
  Database,
  Eye,
  Loader2,
  RefreshCw,
  Search,
  Table as TableIcon,
} from 'lucide-react';

import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  Input,
  notify,
} from '@/components/ui';
import {
  dataSourcesApi,
  type ColumnMeta,
  type TablePreviewResult,
  type TableSummary,
} from '@/api/data-query';
import ResultTable from './ResultTable';

export interface SchemaExplorerProps {
  dataSourceId: string;
  onSelectTable?: (tableName: string) => void;
  className?: string;
}

export default function SchemaExplorer({
  dataSourceId,
  onSelectTable,
  className = '',
}: SchemaExplorerProps) {
  const [tables, setTables] = useState<TableSummary[]>([]);
  const [loadingTables, setLoadingTables] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [search, setSearch] = useState('');

  // 展开的表名集合
  const [expandedTables, setExpandedTables] = useState<Set<string>>(new Set());
  // 缓存各表的字段信息
  const [columnsCache, setColumnsCache] = useState<Record<string, ColumnMeta[]>>({});
  const [loadingCols, setLoadingCols] = useState<Record<string, boolean>>({});

  // 样例数据预览弹窗状态
  const [previewData, setPreviewData] = useState<TablePreviewResult | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);

  // 加载表列表
  const loadTables = useCallback(async () => {
    if (!dataSourceId) return;
    setLoadingTables(true);
    try {
      const data = await dataSourcesApi.getTables(dataSourceId);
      setTables(data);
    } catch (err) {
      notify.error(`获取表列表失败: ${String(err)}`);
    } finally {
      setLoadingTables(false);
    }
  }, [dataSourceId]);

  useEffect(() => {
    loadTables();
  }, [loadTables]);

  // 刷新 Schema 缓存
  const handleRefreshSchema = async () => {
    if (!dataSourceId) return;
    setRefreshing(true);
    try {
      await dataSourcesApi.refreshSchema(dataSourceId);
      notify.success('表结构元数据刷新成功');
      await loadTables();
    } catch (err) {
      notify.error(`刷新表结构失败: ${String(err)}`);
    } finally {
      setRefreshing(false);
    }
  };

  // 切换表展开状态并按需加载字段
  const toggleTable = async (tableName: string) => {
    const next = new Set(expandedTables);
    if (next.has(tableName)) {
      next.delete(tableName);
      setExpandedTables(next);
      return;
    }

    next.add(tableName);
    setExpandedTables(next);

    if (!columnsCache[tableName]) {
      setLoadingCols((prev) => ({ ...prev, [tableName]: true }));
      try {
        const cols = await dataSourcesApi.getColumns(dataSourceId, tableName);
        setColumnsCache((prev) => ({ ...prev, [tableName]: cols }));
      } catch (err) {
        notify.error(`获取表 ${tableName} 字段结构失败: ${String(err)}`);
      } finally {
        setLoadingCols((prev) => ({ ...prev, [tableName]: false }));
      }
    }
  };

  // 预览表数据
  const handlePreviewTable = async (tableName: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setPreviewLoading(true);
    setPreviewOpen(true);
    try {
      const data = await dataSourcesApi.previewTable(dataSourceId, tableName, 20);
      setPreviewData(data);
    } catch (err) {
      notify.error(`预览表 ${tableName} 数据失败: ${String(err)}`);
      setPreviewOpen(false);
    } finally {
      setPreviewLoading(false);
    }
  };

  // 过滤表列表
  const filteredTables = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return tables;
    return tables.filter(
      (t) =>
        t.name.toLowerCase().includes(q) ||
        (t.comment && t.comment.toLowerCase().includes(q))
    );
  }, [tables, search]);

  return (
    <div className={`flex flex-col h-full border border-[#eceef1] rounded-[12px] bg-white overflow-hidden ${className}`}>
      {/* 头部标题与操作 */}
      <div className="flex items-center justify-between px-3 py-2.5 border-b border-[#eceef1] bg-[#fcfcfd]">
        <div className="flex items-center gap-1.5 text-xs font-semibold text-[#18181a]">
          <Database className="w-3.5 h-3.5 text-[#0066cc]" />
          <span>库表结构 (Schema)</span>
          <span className="text-[11px] font-normal text-muted-foreground">({tables.length})</span>
        </div>
        <Button
          variant="ghost"
          size="xs"
          className="h-6 w-6 p-0 text-muted-foreground hover:text-foreground"
          onClick={handleRefreshSchema}
          disabled={refreshing || loadingTables}
          title="刷新元数据缓存"
        >
          <RefreshCw className={`w-3 h-3 ${refreshing ? 'animate-spin' : ''}`} />
        </Button>
      </div>

      {/* 搜索框 */}
      <div className="p-2 border-b border-[#eceef1]">
        <div className="relative">
          <Search className="absolute left-2.5 top-2 w-3.5 h-3.5 text-muted-foreground" />
          <Input
            placeholder="搜索表名或描述..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="h-7 text-xs pl-8 pr-2"
          />
        </div>
      </div>

      {/* 列表区 */}
      <div className="flex-1 overflow-y-auto p-2 space-y-1 text-xs">
        {loadingTables ? (
          <div className="flex items-center justify-center py-8 text-muted-foreground gap-1.5">
            <Loader2 className="w-4 h-4 animate-spin" />
            <span>加载表列表中...</span>
          </div>
        ) : filteredTables.length === 0 ? (
          <div className="text-center py-6 text-muted-foreground">
            {search ? '未搜索到匹配的表' : '暂无可访问的表或无数据表'}
          </div>
        ) : (
          filteredTables.map((t) => {
            const isExpanded = expandedTables.has(t.name);
            const cols = columnsCache[t.name] || [];
            const isColLoading = loadingCols[t.name];

            return (
              <div
                key={t.name}
                className="border border-[#f0f1f3] rounded-md overflow-hidden bg-[#fafafa]/50"
              >
                {/* 表头项 */}
                <div
                  className="flex items-center justify-between px-2 py-1.5 hover:bg-[#f2f4f8] cursor-pointer select-none transition-colors"
                  onClick={() => toggleTable(t.name)}
                >
                  <div className="flex items-center gap-1.5 min-w-0 flex-1">
                    {isExpanded ? (
                      <ChevronDown className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                    ) : (
                      <ChevronRight className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                    )}
                    <TableIcon className="w-3.5 h-3.5 text-[#555a68] shrink-0" />
                    <span
                      className="font-medium text-[#1c2028] truncate hover:underline"
                      onClick={(e) => {
                        if (onSelectTable) {
                          e.stopPropagation();
                          onSelectTable(t.name);
                        }
                      }}
                      title="点击插入表名"
                    >
                      {t.name}
                    </span>
                    {t.comment && (
                      <span className="text-[11px] text-muted-foreground truncate" title={t.comment}>
                        · {t.comment}
                      </span>
                    )}
                  </div>

                  <div className="flex items-center gap-1 shrink-0 ml-2">
                    {t.row_count_estimate > 0 && (
                      <span className="text-[10px] text-muted-foreground px-1 py-0.5 bg-white rounded border border-[#e5e7eb]">
                        ~{t.row_count_estimate.toLocaleString()} 行
                      </span>
                    )}
                    <Button
                      variant="ghost"
                      size="xs"
                      className="h-5 px-1 text-[11px] text-[#0066cc] hover:bg-blue-50"
                      onClick={(e) => handlePreviewTable(t.name, e)}
                      title="预览样例数据"
                    >
                      <Eye className="w-3 h-3 mr-0.5" />
                      预览
                    </Button>
                  </div>
                </div>

                {/* 展开的字段列表 */}
                {isExpanded && (
                  <div className="border-t border-[#ebeef3] bg-white px-2 py-1.5">
                    {isColLoading ? (
                      <div className="flex items-center justify-center py-2 text-muted-foreground text-[11px]">
                        <Loader2 className="w-3 h-3 animate-spin mr-1" />
                        <span>读取字段结构...</span>
                      </div>
                    ) : cols.length === 0 ? (
                      <div className="text-[11px] text-muted-foreground py-1 text-center">
                        无字段信息
                      </div>
                    ) : (
                      <div className="space-y-1">
                        {cols.map((col) => (
                          <div
                            key={col.name}
                            className="flex items-center justify-between text-[11px] py-0.5 px-1.5 rounded hover:bg-[#f7f8fa]"
                          >
                            <div className="flex items-center gap-1.5 min-w-0">
                              <span className="font-mono text-[#252830] font-medium truncate">
                                {col.name}
                              </span>
                              <span className="text-muted-foreground font-mono text-[10px]">
                                {col.column_type || col.data_type}
                              </span>
                              {col.comment && (
                                <span className="text-muted-foreground truncate" title={col.comment}>
                                  · {col.comment}
                                </span>
                              )}
                            </div>
                            {!col.is_nullable && (
                              <span className="text-[9px] text-orange-600 font-medium shrink-0 ml-1">
                                NOT NULL
                              </span>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>

      {/* 样例数据预览弹窗 */}
      <Dialog open={previewOpen} onOpenChange={setPreviewOpen}>
        <DialogContent className="max-w-4xl max-h-[85vh] flex flex-col p-6">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-base">
              <TableIcon className="w-4 h-4 text-[#0066cc]" />
              <span>样例数据预览: {previewData?.table || ''}</span>
              <span className="text-xs font-normal text-muted-foreground">
                (已限制前 20 条，只读连接)
              </span>
            </DialogTitle>
            <DialogDescription>
              检查该数据表的样本数据分布与字段值格式，便于编写 SQL。
            </DialogDescription>
          </DialogHeader>

          <div className="flex-1 overflow-auto my-3 min-h-[240px]">
            {previewLoading ? (
              <div className="flex flex-col items-center justify-center h-48 text-muted-foreground gap-2">
                <Loader2 className="w-6 h-6 animate-spin text-[#0066cc]" />
                <span className="text-xs">正在拉取样例数据...</span>
              </div>
            ) : previewData ? (
              <ResultTable
                columns={previewData.columns}
                rows={previewData.rows}
                rowCount={previewData.row_count}
                maxHeight="450px"
              />
            ) : (
              <div className="text-center py-12 text-muted-foreground text-xs">
                暂无返回数据
              </div>
            )}
          </div>

          <div className="flex justify-end gap-2 pt-2 border-t border-[#eceef1]">
            <Button variant="outline" size="sm" onClick={() => setPreviewOpen(false)}>
              关闭
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
