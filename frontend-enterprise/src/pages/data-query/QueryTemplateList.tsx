import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Database, Edit, FileJson, Plus, RefreshCw, Trash2 } from 'lucide-react';

import { DataTable, type DataTableColumn } from '@/components/DataTable';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui';
import { Button } from '@/components/ui/button';
import { notify } from '@/components/ui/app-toast';
import { StatusBadge } from '@/pages/scheduled-tasks/StatusBadge';
import {
  OUTLINE_ACTION_BUTTON_CLASS,
  formatDateTime,
} from '@/lib/enterprise-ui';
import { EnterpriseRoute } from '@/enums/routes';
import {
  dataSourcesApi,
  queryTemplatesApi,
  type DataSource,
  type QueryTemplate,
} from '@/api/data-query';

function TypeBadge({ type }: { type: string }) {
  if (type === 'sql') {
    return <StatusBadge tone="blue">SQL</StatusBadge>;
  }
  if (type === 'http') {
    return <StatusBadge tone="orange">HTTP</StatusBadge>;
  }
  return <StatusBadge tone="gray">{type}</StatusBadge>;
}

function TemplateStatusBadge({ status }: { status: string }) {
  if (status === 'active') {
    return <StatusBadge tone="green">已启用</StatusBadge>;
  }
  if (status === 'draft') {
    return <StatusBadge tone="gray">草稿</StatusBadge>;
  }
  return <StatusBadge tone="gray">{status}</StatusBadge>;
}

export default function QueryTemplateList() {
  const navigate = useNavigate();

  const [templates, setTemplates] = useState<QueryTemplate[]>([]);
  const [dataSources, setDataSources] = useState<DataSource[]>([]);
  const [loading, setLoading] = useState(false);
  const [dataSourceFilter, setDataSourceFilter] = useState<string>('all');
  const [deleteTarget, setDeleteTarget] = useState<QueryTemplate | null>(null);
  const [deleting, setDeleting] = useState(false);

  const dataSourceNameMap = useMemo(() => {
    const map: Record<string, string> = {};
    for (const ds of dataSources) {
      map[ds.id] = ds.name;
    }
    return map;
  }, [dataSources]);

  const loadDataSources = useCallback(async () => {
    try {
      const data = await dataSourcesApi.list();
      setDataSources(data);
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '加载数据源失败');
    }
  }, []);

  const loadTemplates = useCallback(async () => {
    setLoading(true);
    try {
      const data = await queryTemplatesApi.list(
        dataSourceFilter === 'all' ? undefined : dataSourceFilter,
      );
      setTemplates(data);
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '加载查询模板失败');
    } finally {
      setLoading(false);
    }
  }, [dataSourceFilter]);

  useEffect(() => {
    void loadDataSources();
  }, [loadDataSources]);

  useEffect(() => {
    void loadTemplates();
  }, [loadTemplates]);

  const handleCreate = () => {
    navigate(EnterpriseRoute.DataQueryTemplateNew);
  };

  const handleEdit = (row: QueryTemplate) => {
    navigate(EnterpriseRoute.DataQuery + '/templates/' + row.id);
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await queryTemplatesApi.delete(deleteTarget.id);
      notify.success('删除成功');
      setDeleteTarget(null);
      await loadTemplates();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '删除失败');
    } finally {
      setDeleting(false);
    }
  };

  const renderActions = (row: QueryTemplate) => (
    <div className="flex items-center justify-end gap-[8px]">
      <Button
        variant="outline"
        size="sm"
        onClick={() => handleEdit(row)}
        className={OUTLINE_ACTION_BUTTON_CLASS + ' h-[28px] px-[12px] text-[11px]'}
      >
        <Edit className="mr-[4px] size-[12px]" />
        编辑
      </Button>
      <Button
        variant="outline"
        size="sm"
        onClick={() => setDeleteTarget(row)}
        className={OUTLINE_ACTION_BUTTON_CLASS + ' h-[28px] px-[12px] text-[11px] text-[#d20b0b] hover:text-[#b80909]'}
      >
        <Trash2 className="mr-[4px] size-[12px]" />
        删除
      </Button>
    </div>
  );

  const columns: DataTableColumn<QueryTemplate>[] = [
    {
      key: 'name',
      title: '名称',
      width: 220,
      className: 'text-[#18181a]',
      render: (row) => (
        <div className="flex min-w-0 flex-col gap-[2px]">
          <span
            className="truncate font-medium leading-[18px] text-[#18181a]"
            title={row.name}
          >
            {row.name}
          </span>
          {row.description && (
            <span
              className="truncate text-[#858b9c]"
              title={row.description}
            >
              {row.description}
            </span>
          )}
        </div>
      ),
    },
    {
      key: 'data_source_id',
      title: '数据源',
      width: 140,
      render: (row) => dataSourceNameMap[row.data_source_id] || row.data_source_id,
    },
    {
      key: 'query_type',
      title: '类型',
      width: 80,
      render: (row) => <TypeBadge type={row.query_type} />,
    },
    {
      key: 'status',
      title: '状态',
      width: 90,
      render: (row) => <TemplateStatusBadge status={row.status} />,
    },
    {
      key: 'cache_ttl',
      title: '缓存(秒)',
      width: 90,
      align: 'center',
      render: (row) => row.cache_ttl,
    },
    {
      key: 'updated_at',
      title: '更新时间',
      width: 160,
      render: (row) => formatDateTime(row.updated_at),
    },
    {
      key: 'actions',
      title: '操作',
      width: 180,
      align: 'right',
      sticky: 'right',
      render: (row) => renderActions(row),
    },
  ];

  return (
    <div className="flex flex-col gap-[16px]">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-[8px]">
          <FileJson className="size-[16px] text-[#757f9c]" />
          <span className="text-[14px] font-medium text-[#18181a]">查询模板</span>
          <span className="text-[12px] text-[#858b9c]">
            共 {templates.length} 个
          </span>
        </div>
        <div className="flex items-center gap-[12px]">
          <Select
            value={dataSourceFilter}
            onValueChange={setDataSourceFilter}
          >
            <SelectTrigger size="sm" className="w-[180px]">
              <SelectValue placeholder="选择数据源" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部数据源</SelectItem>
              {dataSources.map((ds) => (
                <SelectItem key={ds.id} value={ds.id}>
                  {ds.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            variant="outline"
            onClick={() => void loadTemplates()}
            disabled={loading}
            className={OUTLINE_ACTION_BUTTON_CLASS}
          >
            <RefreshCw className={`size-[14px] ${loading ? 'animate-spin' : ''}`} />
            刷新
          </Button>
          <Button
            onClick={handleCreate}
            className="h-[34px] gap-[4px] rounded-[10px] bg-[#18181a] px-[20px] text-[12px] font-normal text-white hover:bg-[#303030]"
          >
            <Plus className="size-[14px]" />
            新建模板
          </Button>
        </div>
      </div>

      <DataTable
        aria-label="查询模板列表"
        columns={columns}
        data={templates}
        rowKey={(row) => row.id}
        loading={loading}
        emptyText={
          <div className="flex flex-col items-center gap-[8px]">
            <Database className="size-[32px] text-[#c0c6d4]" />
            <span>暂无查询模板，点击「新建模板」创建一个吧</span>
          </div>
        }
      />

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        loading={deleting}
        title={deleteTarget ? `删除查询模板「${deleteTarget.name}」？` : ''}
        description="删除后，相关查询配置将无法恢复，操作不可撤销。"
        confirmText="删除"
        onConfirm={() => void handleDelete()}
      />
    </div>
  );
}
