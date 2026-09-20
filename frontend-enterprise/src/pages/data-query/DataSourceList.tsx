import { useCallback, useEffect, useState } from 'react';
import { Database, Edit, FlaskConical, Loader2, Plus, RefreshCw, Trash2 } from 'lucide-react';

import { DataTable, type DataTableColumn } from '@/components/DataTable';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui';
import { Button } from '@/components/ui/button';
import { notify } from '@/components/ui/app-toast';
import { StatusBadge } from '@/pages/scheduled-tasks/StatusBadge';
import {
  MENU_CONTENT_CLASS,
  MENU_ITEM_CLASS,
  MENU_ITEM_DANGER_CLASS,
  OUTLINE_ACTION_BUTTON_CLASS,
  formatDateTime,
} from '@/lib/enterprise-ui';
import { dataSourcesApi, type DataSource } from '@/api/data-query';
import DataSourceDialog from './DataSourceDialog';

function TypeBadge({ type }: { type: string }) {
  if (type === 'mysql') {
    return <StatusBadge tone="blue">MySQL</StatusBadge>;
  }
  if (type === 'http_api') {
    return <StatusBadge tone="orange">HTTP API</StatusBadge>;
  }
  return <StatusBadge tone="gray">{type}</StatusBadge>;
}

function StatusBadgeCell({ status }: { status: string }) {
  if (status === 'active') {
    return <StatusBadge tone="green">正常</StatusBadge>;
  }
  if (status === 'error') {
    return <StatusBadge tone="red">异常</StatusBadge>;
  }
  return <StatusBadge tone="gray">{status}</StatusBadge>;
}

export default function DataSourceList() {
  const [items, setItems] = useState<DataSource[]>([]);
  const [loading, setLoading] = useState(false);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<DataSource | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<DataSource | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await dataSourcesApi.list();
      setItems(data);
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '加载数据源失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const handleTest = async (row: DataSource) => {
    setTestingId(row.id);
    try {
      const result = await dataSourcesApi.test(row.id);
      if (result.success) {
        notify.success('连接成功');
      } else {
        notify.error(result.message || '连接失败');
      }
      await load();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '测试连接失败');
      await load();
    } finally {
      setTestingId(null);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await dataSourcesApi.delete(deleteTarget.id);
      notify.success('删除成功');
      setDeleteTarget(null);
      await load();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '删除失败');
    } finally {
      setDeleting(false);
    }
  };

  const handleCreate = () => {
    setCreateOpen(true);
  };

  const handleEdit = (row: DataSource) => {
    setEditTarget(row);
  };

  const renderActions = (row: DataSource) => {
    const isTesting = testingId === row.id;
    return (
      <div className="flex items-center justify-end gap-[8px]">
        <Button
          variant="outline"
          size="sm"
          onClick={() => void handleTest(row)}
          disabled={isTesting}
          className={OUTLINE_ACTION_BUTTON_CLASS + ' h-[28px] px-[12px] text-[11px]'}
        >
          {isTesting ? (
            <Loader2 className="mr-[4px] size-[12px] animate-spin" />
          ) : (
            <FlaskConical className="mr-[4px] size-[12px]" />
          )}
          测试连接
        </Button>
        <DropdownMenu>
          <DropdownMenuTrigger
            aria-label="更多操作"
            className="grid size-7 place-items-center rounded-[8px] text-[#1a71ff] transition-colors outline-none hover:bg-black/5 hover:text-[#4a8dff] focus-visible:bg-black/5"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="5" r="1" />
              <circle cx="12" cy="12" r="1" />
              <circle cx="12" cy="19" r="1" />
            </svg>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className={MENU_CONTENT_CLASS}>
            <DropdownMenuItem className={MENU_ITEM_CLASS} onSelect={() => handleEdit(row)}>
              <Edit className="size-[14px]" />
              编辑
            </DropdownMenuItem>
            <DropdownMenuSeparator className="my-[2px] bg-[#eef0f4]" />
            <DropdownMenuItem
              variant="destructive"
              className={MENU_ITEM_DANGER_CLASS}
              onSelect={() => setDeleteTarget(row)}
            >
              <Trash2 className="size-[14px]" />
              删除
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    );
  };

  const columns: DataTableColumn<DataSource>[] = [
    {
      key: 'name',
      title: '名称',
      width: 200,
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
      key: 'type',
      title: '类型',
      width: 100,
      render: (row) => <TypeBadge type={row.type} />,
    },
    {
      key: 'status',
      title: '状态',
      width: 90,
      render: (row) => <StatusBadgeCell status={row.status} />,
    },
    {
      key: 'read_only',
      title: '只读',
      width: 80,
      align: 'center',
      render: (row) => (row.read_only ? '是' : '否'),
    },
    {
      key: 'last_test_at',
      title: '最后测试',
      width: 160,
      render: (row) => row.last_test_at ? formatDateTime(row.last_test_at) : '-',
    },
    {
      key: 'created_at',
      title: '创建时间',
      width: 160,
      render: (row) => formatDateTime(row.created_at),
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
          <Database className="size-[16px] text-[#757f9c]" />
          <span className="text-[14px] font-medium text-[#18181a]">数据源列表</span>
          <span className="text-[12px] text-[#858b9c]">
            共 {items.length} 个
          </span>
        </div>
        <div className="flex items-center gap-[12px]">
          <Button
            variant="outline"
            onClick={() => void load()}
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
            新建数据源
          </Button>
        </div>
      </div>

      <DataTable
        aria-label="数据源列表"
        columns={columns}
        data={items}
        rowKey={(row) => row.id}
        loading={loading}
        emptyText={
          <div className="flex flex-col items-center gap-[8px]">
            <Database className="size-[32px] text-[#c0c6d4]" />
            <span>暂无数据源，点击「新建数据源」创建一个吧</span>
          </div>
        }
      />

      <DataSourceDialog
        open={createOpen}
        dataSource={null}
        onOpenChange={setCreateOpen}
        onSaved={() => void load()}
      />

      <DataSourceDialog
        open={Boolean(editTarget)}
        dataSource={editTarget}
        onOpenChange={(open) => !open && setEditTarget(null)}
        onSaved={() => void load()}
      />

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        loading={deleting}
        title={deleteTarget ? `删除数据源「${deleteTarget.name}」？` : ''}
        description="删除后，引用该数据源的查询模板将无法使用，操作不可撤销。"
        confirmText="删除"
        onConfirm={() => void handleDelete()}
      />
    </div>
  );
}
