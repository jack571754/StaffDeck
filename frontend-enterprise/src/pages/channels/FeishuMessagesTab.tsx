import { useCallback, useEffect, useState } from 'react';
import {
  AlertCircle,
  Bot,
  CheckCircle2,
  Copy,
  RotateCcw,
  RotateCw,
  Search,
  Send,
} from 'lucide-react';

import { api, TENANT_ID } from '@/api/client';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { DataTable, type DataTableColumn } from '@/components/DataTable';
import { Paginator } from '@/components/Paginator';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  Input,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui';
import { notify } from '@/components/ui/app-toast';
import { Button as UIButton } from '@/components/ui/button';
import { copyTextToClipboard } from '@/lib/clipboard';
import { cn } from '@/lib/utils';
import type {
  FeishuOutboundMessagePage,
  FeishuOutboundMessageRead,
  FeishuOutboundStatsRead,
} from '@/types';
import { StatusBadge } from '../scheduled-tasks/StatusBadge';
import { formatTime, type BadgeTone } from '../scheduled-tasks/shared';

const PRIMARY_BUTTON_CLASS =
  'h-8 gap-1 rounded-[10px] bg-[#18181a] px-5 text-[12px] font-normal text-white hover:bg-[#303030]';
const OUTLINE_BUTTON_CLASS =
  'h-8 gap-1 rounded-[10px] border-[#e3e7f1] px-3 text-[12px] font-normal text-[#464c5e] hover:bg-[#f6f6f6] hover:text-[#18181a]';

const STATUS_MAP: Record<string, { tone: BadgeTone; text: string }> = {
  delivered: { tone: 'green', text: '已送达' },
  failed: { tone: 'red', text: '发送失败' },
  recalled: { tone: 'orange', text: '已撤回' },
};

export default function FeishuMessagesTab() {
  const [messages, setMessages] = useState<FeishuOutboundMessageRead[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [stats, setStats] = useState<FeishuOutboundStatsRead | null>(null);
  const [statsLoading, setStatsLoading] = useState(false);

  // Filters & Pagination
  const [page, setPage] = useState(1);
  const pageSize = 10;
  const [channelType, setChannelType] = useState<'all' | 'app_bot' | 'webhook'>('all');
  const [statusFilter, setStatusFilter] = useState<'all' | 'delivered' | 'failed' | 'recalled'>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [activeSearch, setActiveSearch] = useState('');

  // Modals & Actions
  const [detailMessage, setDetailMessage] = useState<FeishuOutboundMessageRead | null>(null);
  const [recallTarget, setRecallTarget] = useState<FeishuOutboundMessageRead | null>(null);
  const [recalling, setRecalling] = useState(false);

  const fetchStats = useCallback(async () => {
    setStatsLoading(true);
    try {
      const data = await api.get<FeishuOutboundStatsRead>(
        `/api/enterprise/channels/feishu/messages/stats?tenant_id=${TENANT_ID}`,
      );
      setStats(data);
    } catch {
      // stats error fallback silently
    } finally {
      setStatsLoading(false);
    }
  }, []);

  const fetchMessages = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        tenant_id: TENANT_ID,
        offset: String((page - 1) * pageSize),
        limit: String(pageSize),
      });
      if (channelType !== 'all') {
        params.append('channel_type', channelType);
      }
      if (statusFilter !== 'all') {
        params.append('status', statusFilter);
      }
      if (activeSearch.trim()) {
        params.append('search', activeSearch.trim());
      }

      const res = await api.get<FeishuOutboundMessagePage>(
        `/api/enterprise/channels/feishu/messages?${params.toString()}`,
      );
      setMessages(res.items || []);
      setTotal(res.total || 0);
    } catch (err) {
      notify.error(err instanceof Error ? err.message : '获取飞书消息列表失败');
    } finally {
      setLoading(false);
    }
  }, [page, channelType, statusFilter, activeSearch]);

  useEffect(() => {
    void fetchStats();
  }, [fetchStats]);

  useEffect(() => {
    void fetchMessages();
  }, [fetchMessages]);

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setPage(1);
    setActiveSearch(searchQuery);
  };

  const handleRecallConfirm = async () => {
    if (!recallTarget) return;
    setRecalling(true);
    try {
      await api.post(
        `/api/enterprise/channels/feishu/messages/${recallTarget.id}/recall?tenant_id=${TENANT_ID}`,
        {},
      );
      notify.success('消息已成功从飞书撤回');
      setRecallTarget(null);
      void fetchStats();
      void fetchMessages();
    } catch (err) {
      notify.error(err instanceof Error ? err.message : '撤回消息失败');
    } finally {
      setRecalling(false);
    }
  };

  const pageCount = Math.max(1, Math.ceil(total / pageSize));

  const columns: DataTableColumn<FeishuOutboundMessageRead>[] = [
    {
      key: 'created_at',
      title: '触发时间',
      width: 160,
      render: (row) => (
        <span className="text-[12px] text-[#464c5e]">
          {formatTime(row.created_at)}
        </span>
      ),
    },
    {
      key: 'source',
      title: '来源定时任务',
      width: 170,
      render: (row) => (
        <span
          className="truncate text-[12px] font-medium text-[#18181a]"
          title={row.scheduled_task_title || row.scheduled_task_id || '独立调用'}
        >
          {row.scheduled_task_title || (row.scheduled_task_id ? `任务 #${row.scheduled_task_id.slice(-6)}` : '独立/手动调用')}
        </span>
      ),
    },
    {
      key: 'channel_type',
      title: '推送通道',
      width: 140,
      render: (row) => {
        if (row.channel_type === 'app_bot') {
          return (
            <span className="inline-flex items-center gap-[4px] rounded-[6px] bg-[#e8f0ff] px-[6px] py-[2px] text-[11px] font-medium text-[#1a71ff]">
              <Bot className="size-[12px]" />
              自建应用
            </span>
          );
        }
        return (
          <span className="inline-flex items-center gap-[4px] rounded-[6px] bg-[#f5f0ff] px-[6px] py-[2px] text-[11px] font-medium text-[#722ed1]">
            <Send className="size-[11px]" />
            群 Webhook
          </span>
        );
      },
    },
    {
      key: 'target',
      title: '接收目标',
      width: 180,
      render: (row) => (
        <div className="flex flex-col gap-[2px]">
          <span className="truncate text-[12px] font-medium text-[#18181a]" title={row.target_name || row.target_identifier}>
            {row.target_name || '群聊 / 会话'}
          </span>
          <span className="truncate text-[11px] text-[#858b9c]" title={row.target_identifier}>
            {row.target_identifier.length > 25
              ? `${row.target_identifier.slice(0, 10)}...${row.target_identifier.slice(-8)}`
              : row.target_identifier}
          </span>
        </div>
      ),
    },
    {
      key: 'title',
      title: '消息标题 / 卡片内容',
      render: (row) => (
        <span className="line-clamp-1 text-[12px] text-[#18181a]" title={row.title || '卡片消息'}>
          {row.title || '卡片消息'}
        </span>
      ),
    },
    {
      key: 'status',
      title: '状态',
      width: 100,
      render: (row) => {
        const conf = STATUS_MAP[row.status] || { tone: 'gray' as BadgeTone, text: row.status };
        return <StatusBadge tone={conf.tone}>{conf.text}</StatusBadge>;
      },
    },
    {
      key: 'actions',
      title: '操作',
      width: 150,
      align: 'right',
      render: (row) => {
        const isRecalled = row.status === 'recalled';
        const isWebhook = row.channel_type === 'webhook';

        return (
          <div className="flex items-center justify-end gap-[6px]">
            <UIButton
              variant="outline"
              size="sm"
              onClick={() => setDetailMessage(row)}
              className="h-[26px] px-[8px] text-[11px] text-[#464c5e] hover:bg-[#f6f6f6]"
            >
              卡片详情
            </UIButton>

            {isRecalled ? (
              <span className="text-[11px] text-[#858b9c]">已撤回</span>
            ) : isWebhook ? (
              <TooltipProvider>
                <Tooltip delayDuration={150}>
                  <TooltipTrigger asChild>
                    <span>
                      <UIButton
                        variant="outline"
                        size="sm"
                        disabled
                        className="h-[26px] cursor-not-allowed px-[8px] text-[11px] text-[#b0b4be] opacity-50"
                      >
                        撤回
                      </UIButton>
                    </span>
                  </TooltipTrigger>
                  <TooltipContent side="top" className="max-w-[240px] text-[11px]">
                    飞书群 Webhook 机器人不支持 API 撤回，需在群内由管理员手动撤回。
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            ) : row.can_recall ? (
              <UIButton
                variant="outline"
                size="sm"
                onClick={() => setRecallTarget(row)}
                className="h-[26px] border-[#ffa39e] px-[8px] text-[11px] text-[#d20b0b] hover:bg-[#fff1f0]"
              >
                撤回
              </UIButton>
            ) : (
              <UIButton
                variant="outline"
                size="sm"
                disabled
                className="h-[26px] cursor-not-allowed px-[8px] text-[11px] text-[#b0b4be] opacity-50"
              >
                撤回
              </UIButton>
            )}
          </div>
        );
      },
    },
  ];

  return (
    <div className="mt-[20px] flex flex-col gap-[20px]">
      {/* 顶部指标统计栏 */}
      <div className="grid grid-cols-1 gap-[12px] sm:grid-cols-2 lg:grid-cols-4">
        {/* 今日推送 */}
        <div className="flex items-center gap-[14px] rounded-[14px] border border-[#eef0f4] bg-white p-[16px] shadow-[0_1px_2px_rgba(0,0,0,0.02)]">
          <div className="flex size-[40px] shrink-0 items-center justify-center rounded-[10px] bg-[#f2f3f7] text-[#18181a]">
            <Send className="size-[18px]" />
          </div>
          <div className="flex flex-col">
            <span className="text-[12px] text-[#858b9c]">今日推送总数</span>
            <span className="text-[22px] font-semibold text-[#18181a]">
              {statsLoading ? '...' : stats?.total_today ?? 0}
            </span>
          </div>
        </div>

        {/* 成功送达 */}
        <div className="flex items-center gap-[14px] rounded-[14px] border border-[#eef0f4] bg-white p-[16px] shadow-[0_1px_2px_rgba(0,0,0,0.02)]">
          <div className="flex size-[40px] shrink-0 items-center justify-center rounded-[10px] bg-[#e9f7ef] text-[#2cb360]">
            <CheckCircle2 className="size-[18px]" />
          </div>
          <div className="flex flex-col">
            <span className="text-[12px] text-[#858b9c]">成功送达</span>
            <span className="text-[22px] font-semibold text-[#2cb360]">
              {statsLoading ? '...' : stats?.delivered_today ?? 0}
            </span>
          </div>
        </div>

        {/* 发送失败 */}
        <div className="flex items-center gap-[14px] rounded-[14px] border border-[#eef0f4] bg-white p-[16px] shadow-[0_1px_2px_rgba(0,0,0,0.02)]">
          <div className="flex size-[40px] shrink-0 items-center justify-center rounded-[10px] bg-[#fce7e7] text-[#d20b0b]">
            <AlertCircle className="size-[18px]" />
          </div>
          <div className="flex flex-col">
            <span className="text-[12px] text-[#858b9c]">发送失败</span>
            <span className="text-[22px] font-semibold text-[#d20b0b]">
              {statsLoading ? '...' : stats?.failed_today ?? 0}
            </span>
          </div>
        </div>

        {/* 已撤回 */}
        <div className="flex items-center gap-[14px] rounded-[14px] border border-[#eef0f4] bg-white p-[16px] shadow-[0_1px_2px_rgba(0,0,0,0.02)]">
          <div className="flex size-[40px] shrink-0 items-center justify-center rounded-[10px] bg-[#fff2e5] text-[#ff7f00]">
            <RotateCcw className="size-[18px]" />
          </div>
          <div className="flex flex-col">
            <span className="text-[12px] text-[#858b9c]">已撤回消息</span>
            <span className="text-[22px] font-semibold text-[#ff7f00]">
              {statsLoading ? '...' : stats?.recalled_today ?? 0}
            </span>
          </div>
        </div>
      </div>

      {/* 筛选与搜索工具条 */}
      <div className="flex flex-wrap items-center justify-between gap-[12px] rounded-[14px] border border-[#eef0f4] bg-white p-[14px]">
        <div className="flex flex-wrap items-center gap-[10px]">
          {/* 通道类型 */}
          <div className="flex items-center gap-[6px]">
            <span className="text-[12px] text-[#858b9c]">通道:</span>
            <Select
              value={channelType}
              onValueChange={(val) => {
                setChannelType(val as typeof channelType);
                setPage(1);
              }}
            >
              <SelectTrigger className="h-[32px] w-[130px] rounded-[8px] border-[#eef0f4] text-[12px]">
                <SelectValue placeholder="全部通道" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部通道</SelectItem>
                <SelectItem value="app_bot">自建应用机器人</SelectItem>
                <SelectItem value="webhook">群 Webhook</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {/* 发送状态 */}
          <div className="flex items-center gap-[6px]">
            <span className="text-[12px] text-[#858b9c]">状态:</span>
            <Select
              value={statusFilter}
              onValueChange={(val) => {
                setStatusFilter(val as typeof statusFilter);
                setPage(1);
              }}
            >
              <SelectTrigger className="h-[32px] w-[110px] rounded-[8px] border-[#eef0f4] text-[12px]">
                <SelectValue placeholder="全部状态" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部状态</SelectItem>
                <SelectItem value="delivered">已送达</SelectItem>
                <SelectItem value="failed">发送失败</SelectItem>
                <SelectItem value="recalled">已撤回</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {/* 搜索框 */}
          <form onSubmit={handleSearchSubmit} className="flex items-center gap-[6px]">
            <div className="relative">
              <Search className="absolute top-[8px] left-[10px] size-[14px] text-[#858b9c]" />
              <Input
                type="text"
                placeholder="搜索标题 / 群名 / ID"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="h-[32px] w-[180px] rounded-[8px] border-[#eef0f4] pl-[30px] text-[12px] focus:w-[220px] transition-all"
              />
            </div>
            <UIButton type="submit" variant="outline" className={OUTLINE_BUTTON_CLASS}>
              搜索
            </UIButton>
            {activeSearch && (
              <UIButton
                type="button"
                variant="ghost"
                onClick={() => {
                  setSearchQuery('');
                  setActiveSearch('');
                  setPage(1);
                }}
                className="h-[32px] px-[8px] text-[12px] text-[#858b9c] hover:text-[#18181a]"
              >
                重置
              </UIButton>
            )}
          </form>
        </div>

        {/* 刷新按钮 */}
        <div className="flex items-center gap-[8px]">
          <UIButton
            variant="outline"
            onClick={() => {
              void fetchStats();
              void fetchMessages();
            }}
            disabled={loading}
            className={OUTLINE_BUTTON_CLASS}
          >
            <RotateCw className={cn('size-[13px]', loading && 'animate-spin')} />
            刷新
          </UIButton>
        </div>
      </div>

      {/* 数据表格 */}
      <div className="rounded-[14px] border border-[#eef0f4] bg-white p-[4px] shadow-[0_1px_2px_rgba(0,0,0,0.02)]">
        <DataTable
          columns={columns}
          data={messages}
          rowKey={(row) => row.id}
          loading={loading}
          emptyText="暂无飞书消息推送记录"
          size="compact"
          striped
          bordered
        />

        {/* 分页控制栏 */}
        <div className="flex items-center justify-between px-[16px] py-[12px]">
          <span className="text-[12px] text-[#858b9c]">
            {total > 0
              ? `显示 ${(page - 1) * pageSize + 1} - ${Math.min(page * pageSize, total)} 条，共 ${total} 条`
              : '共 0 条'}
          </span>
          <Paginator
            page={page}
            pageCount={pageCount}
            onChange={setPage}
          />
        </div>
      </div>

      {/* 消息详情与卡片预览弹窗 */}
      <Dialog open={Boolean(detailMessage)} onOpenChange={(open) => !open && setDetailMessage(null)}>
        <DialogContent className="max-h-[85vh] w-[90vw] max-w-[720px] overflow-hidden rounded-[16px] p-0">
          <DialogHeader className="border-b border-[#eef0f4] px-[24px] py-[16px]">
            <DialogTitle className="flex items-center gap-[8px] text-[15px] font-semibold text-[#18181a]">
              <span>飞书推送消息详情</span>
              {detailMessage && (
                <StatusBadge tone={STATUS_MAP[detailMessage.status]?.tone || 'gray'}>
                  {STATUS_MAP[detailMessage.status]?.text || detailMessage.status}
                </StatusBadge>
              )}
            </DialogTitle>
          </DialogHeader>

          {detailMessage && (
            <div className="flex max-h-[calc(85vh-70px)] flex-col gap-[16px] overflow-y-auto px-[24px] py-[20px]">
              {/* 元数据网格 */}
              <div className="grid grid-cols-2 gap-[12px] rounded-[10px] bg-[#f9fafb] p-[14px] text-[12px]">
                <div className="flex flex-col gap-[2px]">
                  <span className="text-[#858b9c]">消息标题</span>
                  <span className="font-medium text-[#18181a]">{detailMessage.title || '卡片消息'}</span>
                </div>
                <div className="flex flex-col gap-[2px]">
                  <span className="text-[#858b9c]">推送通道</span>
                  <span className="font-medium text-[#18181a]">
                    {detailMessage.channel_type === 'app_bot' ? '自建应用机器人 (im.v1)' : '自定义群机器人 (Webhook)'}
                  </span>
                </div>
                <div className="flex flex-col gap-[2px]">
                  <span className="text-[#858b9c]">接收目标</span>
                  <span className="font-medium text-[#18181a]">
                    {detailMessage.target_name ? `${detailMessage.target_name} (${detailMessage.target_identifier})` : detailMessage.target_identifier}
                  </span>
                </div>
                <div className="flex flex-col gap-[2px]">
                  <span className="text-[#858b9c]">触发时间</span>
                  <span className="font-medium text-[#18181a]">{formatTime(detailMessage.created_at)}</span>
                </div>
                {detailMessage.feishu_message_id && (
                  <div className="col-span-2 flex items-center justify-between gap-[8px] rounded-[6px] border border-[#eef0f4] bg-white px-[10px] py-[6px]">
                    <span className="text-[#858b9c]">飞书 Message ID:</span>
                    <span className="font-mono text-[11px] text-[#18181a]">{detailMessage.feishu_message_id}</span>
                    <UIButton
                      variant="ghost"
                      size="sm"
                      onClick={() => {
                        copyTextToClipboard(detailMessage.feishu_message_id || '');
                        notify.success('已复制 Message ID');
                      }}
                      className="h-[22px] px-[6px] text-[11px]"
                    >
                      <Copy className="size-[12px]" />
                    </UIButton>
                  </div>
                )}
                {detailMessage.recalled_at && (
                  <div className="col-span-2 rounded-[6px] bg-[#fff8e8] px-[10px] py-[6px] text-[#6f4500]">
                    已由 {detailMessage.recalled_by || '管理员'} 于 {formatTime(detailMessage.recalled_at)} 撤回
                  </div>
                )}
                {detailMessage.error_message && (
                  <div className="col-span-2 rounded-[6px] bg-[#fce7e7] p-[10px] text-[#d20b0b]">
                    <span className="font-semibold">错误信息: </span>
                    {detailMessage.error_message}
                  </div>
                )}
              </div>

              {/* 卡片 Payload 内容 */}
              <div className="flex flex-col gap-[8px]">
                <div className="flex items-center justify-between">
                  <span className="text-[13px] font-semibold text-[#18181a]">卡片报文 (Card Payload)</span>
                  <UIButton
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      copyTextToClipboard(JSON.stringify(detailMessage.card_json, null, 2));
                      notify.success('已复制卡片 JSON');
                    }}
                    className="h-[26px] gap-[4px] px-[8px] text-[11px]"
                  >
                    <Copy className="size-[12px]" />
                    复制 JSON
                  </UIButton>
                </div>
                <pre className="max-h-[300px] overflow-auto rounded-[10px] border border-[#eef0f4] bg-[#1e1e24] p-[14px] font-mono text-[11px] leading-[1.6] text-[#e0e0e0]">
                  {JSON.stringify(detailMessage.card_json, null, 2)}
                </pre>
              </div>

              {/* 弹窗底部操作 */}
              <div className="flex items-center justify-end gap-[8px] border-t border-[#eef0f4] pt-[14px]">
                {detailMessage.can_recall && detailMessage.status !== 'recalled' && (
                  <UIButton
                    variant="outline"
                    onClick={() => {
                      setRecallTarget(detailMessage);
                      setDetailMessage(null);
                    }}
                    className="h-[32px] border-[#ffa39e] text-[12px] text-[#d20b0b] hover:bg-[#fff1f0]"
                  >
                    撤回此消息
                  </UIButton>
                )}
                <UIButton
                  onClick={() => setDetailMessage(null)}
                  className={PRIMARY_BUTTON_CLASS}
                >
                  关闭
                </UIButton>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* 撤回确认对话框 */}
      <ConfirmDialog
        open={Boolean(recallTarget)}
        onOpenChange={(open) => !open && setRecallTarget(null)}
        loading={recalling}
        title="确定撤回该飞书消息？"
        description={
          recallTarget
            ? `将通过飞书 open-apis/im/v1/messages 撤回群「${recallTarget.target_name || recallTarget.target_identifier}」中的消息（ID: ${recallTarget.feishu_message_id || recallTarget.id}）。撤回后群成员将无法再看到该卡片。`
            : '确定撤回吗？'
        }
        confirmText="确认撤回"
        onConfirm={() => void handleRecallConfirm()}
      />
    </div>
  );
}
