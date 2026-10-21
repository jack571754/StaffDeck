import { useEffect, useMemo, useState } from 'react';
import {
  AlertCircle,
  Calendar,
  CalendarClock,
  CheckCircle2,
  Clock,
  Copy,
  ExternalLink,
  Eye,
  FileCode,
  Link2,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Send,
  ShieldAlert,
  Sparkles,
  Trash2,
  XCircle,
} from 'lucide-react';

import { api } from '@/api/client';
import { copyTextToClipboard } from '@/lib/clipboard';
import { formatClientDateTime, getClientTimeZone, parseBackendDateTime } from '@/lib/timezone';
import { cn } from '@/lib/utils';
import type { EnterpriseAuthUser } from '@/auth';
import {
  Badge,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Switch,
  Textarea,
} from '@/components/ui';
import { Button as UIButton } from '@/components/ui/button';
import { notify } from '@/components/ui/app-toast';
import {
  DIALOG_CANCEL_BUTTON_CLASS,
  DIALOG_FOOTER_CLASS,
  DIALOG_PRIMARY_BUTTON_CLASS,
  MENU_ITEM_DANGER_CLASS,
  OUTLINE_ACTION_BUTTON_CLASS,
  OUTLINE_ACTION_BUTTON_SM_CLASS,
} from '@/lib/enterprise-ui';

const INSPIRATION_PROMPTS = [
  {
    label: '📈 销售与渠道大屏',
    prompt: '分析近 7 天各大区门店销售业绩与自营/达播净销环比，并生成核心 KPI 与达成率表格',
    title: '华东大区销售与渠道大屏',
    renderer: 'sales_report' as const,
  },
  {
    label: '📊 全渠道投放ROI',
    prompt: '汇总近两周抖音、小红书与微信朋友圈渠道投放花费、转化率与综合 ROI 产出比报表',
    title: '全渠道投放转化与ROI看板',
    renderer: 'generic_table' as const,
  },
  {
    label: '📦 供应链库存周转',
    prompt: '统计各中心仓核心商品品类当前在库库存、周转天数及现货满足率，标出需预警品类',
    title: '供应链库存周转与备货分析看板',
    renderer: 'generic_table' as const,
  },
  {
    label: '🎧 客户服务时效大屏',
    prompt: '汇总在线客服与工单处理接入总量、平均响应与解决时效、一次性解决率与用户满意度',
    title: '客户服务质量与工单时效看板',
    renderer: 'generic_table' as const,
  },
];


export type ArtifactShareLinkRead = {
  id: string;
  tenant_id: string;
  session_id: string;
  owner_kind: string;
  owner_id: string;
  path: string;
  title: string | null;
  token: string;
  url: string;
  created_by_user_id: string | null;
  created_by_username: string | null;
  ttl_seconds: number;
  expires_at: string;
  is_revoked: boolean;
  revoked_at: string | null;
  revoked_by_user_id: string | null;
  view_count: number;
  last_viewed_at: string | null;
  created_at: string;
  updated_at: string;
  status: 'active' | 'expiring_soon' | 'expired' | 'revoked';
  remaining_seconds: number;
  bound_task_id?: string | null;
  bound_task_title?: string | null;
};

export type ArtifactShareLinksListResponse = {
  items: ArtifactShareLinkRead[];
  total: number;
  active_count: number;
  expiring_soon_count: number;
  expired_count: number;
  revoked_count: number;
  total_views: number;
};

export type RecentReportItem = {
  session_id: string;
  task_frame_id: string;
  owner_kind: string;
  owner_id: string;
  path: string;
  display_name: string;
  size: number | null;
  created_at: string | null;
};

type ShareLinksPageProps = {
  currentUser: EnterpriseAuthUser;
  onLogout: () => void;
};

function toDateTimeLocalValue(value?: string): string {
  if (!value) return '';
  const d = parseBackendDateTime(value);
  if (Number.isNaN(d.getTime())) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  const year = d.getFullYear();
  const month = pad(d.getMonth() + 1);
  const day = pad(d.getDate());
  const hours = pad(d.getHours());
  const minutes = pad(d.getMinutes());
  return `${year}-${month}-${day}T${hours}:${minutes}`;
}

export default function ShareLinksPage({ currentUser }: ShareLinksPageProps) {
  const [loading, setLoading] = useState(true);
  const [links, setLinks] = useState<ArtifactShareLinkRead[]>([]);
  const [stats, setStats] = useState({
    total: 0,
    active_count: 0,
    expiring_soon_count: 0,
    expired_count: 0,
    revoked_count: 0,
    total_views: 0,
  });

  const [statusFilter, setStatusFilter] = useState<'all' | 'active' | 'expiring_soon' | 'expired' | 'revoked'>('all');
  const [searchQuery, setSearchQuery] = useState('');

  // Modals state
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [extendModalOpen, setExtendModalOpen] = useState(false);
  const [revokeModalOpen, setRevokeModalOpen] = useState(false);
  const [deleteModalOpen, setDeleteModalOpen] = useState(false);
  const [editModalOpen, setEditModalOpen] = useState(false);
  const [selectedLinkForEdit, setSelectedLinkForEdit] = useState<ArtifactShareLinkRead | null>(null);
  const [editForm, setEditForm] = useState({ title: '', expiresAt: '' });
  const [editBaseline, setEditBaseline] = useState({ title: '', expiresAt: '' });
  const [editSubmitting, setEditSubmitting] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  const [selectedLink, setSelectedLink] = useState<ArtifactShareLinkRead | null>(null);
  const [extendDays, setExtendDays] = useState('7');
  const [customExtendDays, setCustomExtendDays] = useState('');

  // Create mode & forms
  const [creationMode, setCreationMode] = useState<'semantic' | 'manual'>('semantic');
  const [generating, setGenerating] = useState(false);
  const [semanticForm, setSemanticForm] = useState({
    prompt: '',
    title: '',
    renderer: 'sales_report' as 'sales_report' | 'generic_table',
    days: '7',
    custom_days: '',
  });

  const [recentReports, setRecentReports] = useState<RecentReportItem[]>([]);
  const [selectedReportKey, setSelectedReportKey] = useState<string>('manual');
  const [createForm, setCreateForm] = useState({
    session_id: '',
    task_frame_id: '',
    path: '',
    title: '',
    days: '7',
    custom_days: '',
  });

  // Scheduled push configuration in create modal
  const [scheduledPush, setScheduledPush] = useState({
    enabled: false,
    schedule_type: 'daily' as 'daily' | 'interval',
    time: '09:00',
    interval_minutes: 60,
    feishu_webhook: '',
    feishu_chat_id: '',
    task_title: '',
  });

  // Bind to scheduled task modal state
  const [bindModalOpen, setBindModalOpen] = useState(false);
  const [selectedLinkForBinding, setSelectedLinkForBinding] = useState<ArtifactShareLinkRead | null>(null);
  const [existingTasks, setExistingTasks] = useState<Array<{ id: string; title: string; schedule_type: string; status: string }>>([]);
  const [loadingExistingTasks, setLoadingExistingTasks] = useState(false);
  const [bindMode, setBindMode] = useState<'existing' | 'new'>('new');
  const [bindSubmitting, setBindSubmitting] = useState(false);
  const [bindForm, setBindForm] = useState({
    selectedTaskId: '',
    task_title: '',
    schedule_type: 'daily' as 'daily' | 'interval',
    time: '09:00',
    interval_minutes: 60,
    feishu_webhook: '',
    feishu_chat_id: '',
  });

  // Load share links
  async function loadShareLinks() {
    try {
      setLoading(true);
      const params = new URLSearchParams();
      if (statusFilter !== 'all') params.append('status', statusFilter);
      if (searchQuery.trim()) params.append('search', searchQuery.trim());

      const res = await api.get<ArtifactShareLinksListResponse>(
        `/api/chat/artifacts/share-links?${params.toString()}`
      );
      setLinks(res.items);
      setStats({
        total: res.total,
        active_count: res.active_count,
        expiring_soon_count: res.expiring_soon_count,
        expired_count: res.expired_count,
        revoked_count: res.revoked_count,
        total_views: res.total_views,
      });
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '加载外链列表失败');
    } finally {
      setLoading(false);
    }
  }

  // Load recent reports for modal
  async function loadRecentReports() {
    try {
      const res = await api.get<RecentReportItem[]>('/api/chat/artifacts/share-links/recent-reports');
      setRecentReports(res);
      if (res.length > 0) {
        setSelectedReportKey(`0`);
        setCreateForm((prev) => ({
          ...prev,
          session_id: res[0].session_id,
          task_frame_id: res[0].task_frame_id,
          path: res[0].path,
          title: res[0].display_name,
        }));
      }
    } catch {
      // Non-fatal, fallback to manual
    }
  }

  useEffect(() => {
    void loadShareLinks();
  }, [statusFilter]);

  function handleSearchSubmit(e: React.FormEvent) {
    e.preventDefault();
    void loadShareLinks();
  }

  // Copy link
  async function handleCopy(link: ArtifactShareLinkRead) {
    try {
      await copyTextToClipboard(link.url);
      notify.success('外链已复制到剪贴板');
    } catch {
      notify.error('复制链接失败');
    }
  }

  // Open Preview
  function handlePreview(link: ArtifactShareLinkRead) {
    // Relative or direct
    window.open(link.url, '_blank', 'noopener,noreferrer');
  }

  // Open create modal
  function openCreateModal() {
    setCreationMode('semantic');
    setScheduledPush({
      enabled: false,
      schedule_type: 'daily',
      time: '09:00',
      interval_minutes: 60,
      feishu_webhook: '',
      feishu_chat_id: '',
      task_title: '',
    });
    void loadRecentReports();
    setCreateModalOpen(true);
  }

  // Handle report select change
  function handleReportSelect(key: string) {
    setSelectedReportKey(key);
    if (key === 'manual') {
      setCreateForm((prev) => ({
        ...prev,
        session_id: '',
        task_frame_id: '',
        path: '',
        title: '',
      }));
    } else {
      const idx = parseInt(key, 10);
      const rep = recentReports[idx];
      if (rep) {
        setCreateForm((prev) => ({
          ...prev,
          session_id: rep.session_id,
          task_frame_id: rep.task_frame_id,
          path: rep.path,
          title: rep.display_name,
        }));
      }
    }
  }

  // Semantic report generate submit
  async function handleSemanticGenerateSubmit() {
    const promptText = semanticForm.prompt.trim();
    if (!promptText) {
      notify.error('请输入报表语义需求描述');
      return;
    }
    const days =
      semanticForm.days === 'custom'
        ? parseInt(semanticForm.custom_days, 10) || 7
        : parseInt(semanticForm.days, 10) || 7;

    setGenerating(true);
    try {
      const res = await api.post<ArtifactShareLinkRead>(
        '/api/chat/artifacts/share-links/semantic-generate',
        {
          tenant_id: currentUser.tenant_id,
          prompt: promptText,
          title: semanticForm.title.trim() || undefined,
          renderer: semanticForm.renderer,
          days,
          schedule_push: scheduledPush.enabled
            ? {
                enabled: true,
                schedule_type: scheduledPush.schedule_type,
                time: scheduledPush.time,
                interval_minutes: scheduledPush.interval_minutes,
                feishu_webhook: scheduledPush.feishu_webhook.trim() || undefined,
                feishu_chat_id: scheduledPush.feishu_chat_id.trim() || undefined,
                task_title: scheduledPush.task_title.trim() || undefined,
              }
            : undefined,
        }
      );

      if (res.bound_task_title) {
        notify.success(`AI 语义报告已生成，并已创建定时推送任务【${res.bound_task_title}】！`);
      } else {
        notify.success(`AI 语义报告已成功生成并发布（${days}天有效）！`);
      }
      if (res.url) {
        await copyTextToClipboard(res.url);
        notify.success('在线访问外链已复制到剪贴板');
      }
      setCreateModalOpen(false);
      setSemanticForm({
        prompt: '',
        title: '',
        renderer: 'sales_report',
        days: '7',
        custom_days: '',
      });
      setScheduledPush({
        enabled: false,
        schedule_type: 'daily',
        time: '09:00',
        interval_minutes: 60,
        feishu_webhook: '',
        feishu_chat_id: '',
        task_title: '',
      });
      void loadShareLinks();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : 'AI 语义报表生成失败');
    } finally {
      setGenerating(false);
    }
  }

  // Create share link submit (manual mode)
  async function handleCreateSubmit() {
    if (!createForm.session_id.trim()) {
      notify.error('请输入会话 ID');
      return;
    }
    if (!createForm.path.trim()) {
      notify.error('请输入报告路径');
      return;
    }

    const days =
      createForm.days === 'custom'
        ? parseInt(createForm.custom_days, 10) || 7
        : parseInt(createForm.days, 10) || 7;

    try {
      const res = await api.post<{ token: string; url: string; link_id: string }>(
        '/api/chat/artifacts/share-links',
        {
          tenant_id: currentUser.tenant_id,
          session_id: createForm.session_id.trim(),
          task_frame_id: createForm.task_frame_id.trim() || 'task_default',
          path: createForm.path.trim(),
          title: createForm.title.trim() || undefined,
          days,
        }
      );

      if (scheduledPush.enabled && res.link_id) {
        try {
          await api.post(`/api/chat/artifacts/share-links/${res.link_id}/bind-scheduled-task`, {
            tenant_id: currentUser.tenant_id,
            create_new_task: true,
            task_title: scheduledPush.task_title.trim() || (createForm.title.trim() ? `【定时推送】${createForm.title.trim()}` : undefined),
            schedule_type: scheduledPush.schedule_type,
            time: scheduledPush.time,
            interval_minutes: scheduledPush.interval_minutes,
            feishu_webhook: scheduledPush.feishu_webhook.trim() || undefined,
            feishu_chat_id: scheduledPush.feishu_chat_id.trim() || undefined,
          });
          notify.success('分享链接已生成，并已创建关联定时推送任务！');
        } catch {
          notify.success(`分享链接已生成（${days}天有效）！`);
        }
      } else {
        notify.success(`分享链接已生成（${days}天有效）！`);
      }

      await copyTextToClipboard(res.url);
      setCreateModalOpen(false);
      void loadShareLinks();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '创建分享链接失败');
    }
  }

  // Open bind scheduled task modal
  async function openBindModal(link: ArtifactShareLinkRead) {
    setSelectedLinkForBinding(link);
    setBindForm({
      selectedTaskId: link.bound_task_id || '',
      task_title: `【定时推送】${link.title || 'HTML 业务报告'}`,
      schedule_type: 'daily',
      time: '09:00',
      interval_minutes: 60,
      feishu_webhook: '',
      feishu_chat_id: '',
    });
    setBindMode(link.bound_task_id ? 'existing' : 'new');
    setBindModalOpen(true);
    try {
      setLoadingExistingTasks(true);
      const tasks = await api.get<Array<{ id: string; title: string; schedule_type: string; status: string }>>(
        `/api/enterprise/scheduled-tasks?tenant_id=${currentUser.tenant_id}`
      );
      setExistingTasks(tasks);
      if (tasks.length > 0 && !link.bound_task_id) {
        setBindForm((prev) => ({ ...prev, selectedTaskId: tasks[0].id }));
      }
    } catch {
      // ignore
    } finally {
      setLoadingExistingTasks(false);
    }
  }

  // Submit bind scheduled task
  async function handleBindSubmit() {
    if (!selectedLinkForBinding) return;
    setBindSubmitting(true);
    try {
      if (bindMode === 'existing') {
        if (!bindForm.selectedTaskId) {
          notify.error('请选择要绑定的定时任务');
          return;
        }
        await api.post(`/api/chat/artifacts/share-links/${selectedLinkForBinding.id}/bind-scheduled-task`, {
          tenant_id: currentUser.tenant_id,
          task_id: bindForm.selectedTaskId,
          create_new_task: false,
          feishu_webhook: bindForm.feishu_webhook.trim() || undefined,
          feishu_chat_id: bindForm.feishu_chat_id.trim() || undefined,
        });
        notify.success('已成功绑定到所选定时任务！');
      } else {
        await api.post(`/api/chat/artifacts/share-links/${selectedLinkForBinding.id}/bind-scheduled-task`, {
          tenant_id: currentUser.tenant_id,
          create_new_task: true,
          task_title: bindForm.task_title.trim() || undefined,
          schedule_type: bindForm.schedule_type,
          time: bindForm.time,
          interval_minutes: bindForm.interval_minutes,
          feishu_webhook: bindForm.feishu_webhook.trim() || undefined,
          feishu_chat_id: bindForm.feishu_chat_id.trim() || undefined,
        });
        notify.success('已成功新建定时推送任务并绑定该外链！');
      }
      setBindModalOpen(false);
      void loadShareLinks();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '绑定定时任务失败');
    } finally {
      setBindSubmitting(false);
    }
  }

  // Revoke submit
  async function handleRevokeSubmit() {
    if (!selectedLink) return;
    try {
      await api.post(`/api/chat/artifacts/share-links/${selectedLink.id}/revoke`);
      notify.success('外链已成功作废，后续访问将被拒绝');
      setRevokeModalOpen(false);
      setSelectedLink(null);
      void loadShareLinks();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '作废外链失败');
    }
  }

  // Extend submit
  async function handleExtendSubmit() {
    if (!selectedLink) return;
    const days =
      extendDays === 'custom'
        ? parseInt(customExtendDays, 10) || 7
        : parseInt(extendDays, 10) || 7;

    try {
      const res = await api.post<ArtifactShareLinkRead>(
        `/api/chat/artifacts/share-links/${selectedLink.id}/extend`,
        { days }
      );
      notify.success(`有效期已延长 ${days} 天，并已更新签名链接！`);
      await copyTextToClipboard(res.url);
      setExtendModalOpen(false);
      setSelectedLink(null);
      void loadShareLinks();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '延长有效期失败');
    }
  }

  // Delete submit
  async function handleDeleteSubmit() {
    if (!selectedLink) return;
    try {
      await api.delete(`/api/chat/artifacts/share-links/${selectedLink.id}`);
      notify.success('外链记录已删除');
      setDeleteModalOpen(false);
      setSelectedLink(null);
      void loadShareLinks();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '删除失败');
    }
  }

  // Open edit modal
  function openEditModal(link: ArtifactShareLinkRead) {
    const formattedExpiresAt = toDateTimeLocalValue(link.expires_at);
    const initialTitle = (link.title || '').trim();
    setSelectedLinkForEdit(link);
    setEditForm({
      title: link.title || '',
      expiresAt: formattedExpiresAt,
    });
    setEditBaseline({
      title: initialTitle,
      expiresAt: formattedExpiresAt,
    });
    setEditError(null);
    setEditModalOpen(true);
  }

  // Submit edit
  async function handleEditSubmit() {
    if (!selectedLinkForEdit) return;

    const trimmedTitle = editForm.title.trim();
    if (!trimmedTitle) {
      setEditError('报告名称不能为空');
      return;
    }
    if (trimmedTitle.length > 200) {
      setEditError('报告名称不能超过 200 个字符');
      return;
    }

    const originalTitle = (selectedLinkForEdit.title || '').trim();
    if (originalTitle.startsWith('【演示数据】') && !trimmedTitle.startsWith('【演示数据】')) {
      setEditError('演示数据链接的名称必须保留【演示数据】前缀');
      return;
    }

    const payload: { title?: string; expires_at?: string } = {};

    if (trimmedTitle !== editBaseline.title) {
      payload.title = trimmedTitle;
    }

    if (!selectedLinkForEdit.is_revoked && editForm.expiresAt !== editBaseline.expiresAt) {
      const expDate = new Date(editForm.expiresAt);
      if (Number.isNaN(expDate.getTime()) || expDate.getTime() <= Date.now() + 60 * 1000) {
        setEditError('到期时间必须晚于当前时间');
        return;
      }
      payload.expires_at = expDate.toISOString();
    }

    // Both unchanged
    if (Object.keys(payload).length === 0) {
      setEditModalOpen(false);
      return;
    }

    setEditSubmitting(true);
    setEditError(null);
    try {
      await api.patch<ArtifactShareLinkRead>(
        `/api/chat/artifacts/share-links/${selectedLinkForEdit.id}`,
        payload
      );
      notify.success('外链已更新');
      setEditModalOpen(false);
      void loadShareLinks();
    } catch (error) {
      setEditError(error instanceof Error ? error.message : '更新外链失败');
    } finally {
      setEditSubmitting(false);
    }
  }

  // Format remaining time text
  function formatRemaining(link: ArtifactShareLinkRead) {
    if (link.is_revoked) {
      return { text: '已作废', variant: 'destructive' as const };
    }
    const secs = link.remaining_seconds;
    if (secs <= 0) {
      return { text: '已过期', variant: 'secondary' as const };
    }
    const days = Math.floor(secs / 86400);
    const hours = Math.floor((secs % 86400) / 3600);
    const mins = Math.floor((secs % 3600) / 60);

    if (days > 0) {
      return {
        text: `剩余 ${days}天 ${hours}小时`,
        variant: days <= 1 ? ('warning' as const) : ('default' as const),
      };
    }
    if (hours > 0) {
      return {
        text: `剩余 ${hours}小时 ${mins}分`,
        variant: 'warning' as const,
      };
    }
    return {
      text: `剩余 ${mins}分钟`,
      variant: 'warning' as const,
    };
  }

  return (
    <div className="flex flex-1 flex-col gap-[20px] p-[24px]">
      {/* Page Header */}
      <div className="flex flex-col gap-[8px] sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="flex items-center gap-[10px] text-[20px] font-semibold text-[#18181a]">
            <Link2 className="size-[22px] text-[#1a71ff]" />
            HTML 链接管理
          </h1>
          <p className="mt-[4px] text-[13px] text-[#757f9c]">
            集中配置和管理 HTML 报告外链生命周期，支持设置有效期限（默认7天）、即时作废与访问统计
          </p>
        </div>
        <div className="flex items-center gap-[10px]">
          <UIButton
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void loadShareLinks()}
            className={OUTLINE_ACTION_BUTTON_CLASS}
          >
            <RefreshCw className={cn('size-[14px]', loading && 'animate-spin')} />
            刷新
          </UIButton>
          <UIButton
            type="button"
            size="sm"
            onClick={openCreateModal}
            className="h-[34px] gap-[6px] rounded-[10px] bg-[#1a71ff] px-[16px] text-[13px] font-medium text-white shadow-sm hover:bg-[#0f5ed7]"
          >
            <Sparkles className="size-[15px]" />
            语义新建 HTML 链接
          </UIButton>
        </div>
      </div>

      {/* Stats Summary Cards */}
      <div className="grid grid-cols-2 gap-[14px] sm:grid-cols-3 lg:grid-cols-6">
        <Card className="rounded-[12px] border-[0.5px] border-[#e3e7f1] bg-white p-[14px] shadow-none">
          <div className="text-[12px] text-[#757f9c]">全部外链</div>
          <div className="mt-[6px] text-[22px] font-semibold text-[#18181a]">{stats.total}</div>
        </Card>
        <Card className="rounded-[12px] border-[0.5px] border-[#e3e7f1] bg-white p-[14px] shadow-none">
          <div className="flex items-center gap-[6px] text-[12px] text-[#059669]">
            <span className="size-[8px] rounded-full bg-[#10b981]" />
            有效中
          </div>
          <div className="mt-[6px] text-[22px] font-semibold text-[#059669]">{stats.active_count}</div>
        </Card>
        <Card className="rounded-[12px] border-[0.5px] border-[#e3e7f1] bg-white p-[14px] shadow-none">
          <div className="flex items-center gap-[6px] text-[12px] text-[#d97706]">
            <span className="size-[8px] rounded-full bg-[#f59e0b]" />
            即将到期 (&lt;24h)
          </div>
          <div className="mt-[6px] text-[22px] font-semibold text-[#d97706]">{stats.expiring_soon_count}</div>
        </Card>
        <Card className="rounded-[12px] border-[0.5px] border-[#e3e7f1] bg-white p-[14px] shadow-none">
          <div className="flex items-center gap-[6px] text-[12px] text-[#6b7280]">
            <span className="size-[8px] rounded-full bg-[#9ca3af]" />
            已过期
          </div>
          <div className="mt-[6px] text-[22px] font-semibold text-[#6b7280]">{stats.expired_count}</div>
        </Card>
        <Card className="rounded-[12px] border-[0.5px] border-[#e3e7f1] bg-white p-[14px] shadow-none">
          <div className="flex items-center gap-[6px] text-[12px] text-[#dc2626]">
            <span className="size-[8px] rounded-full bg-[#ef4444]" />
            已作废
          </div>
          <div className="mt-[6px] text-[22px] font-semibold text-[#dc2626]">{stats.revoked_count}</div>
        </Card>
        <Card className="rounded-[12px] border-[0.5px] border-[#e3e7f1] bg-white p-[14px] shadow-none">
          <div className="flex items-center gap-[6px] text-[12px] text-[#2563eb]">
            <Eye className="size-[12px]" />
            累计访问量
          </div>
          <div className="mt-[6px] text-[22px] font-semibold text-[#2563eb]">{stats.total_views}</div>
        </Card>
      </div>

      {/* Filter Toolbar */}
      <div className="flex flex-col gap-[12px] rounded-[14px] border-[0.5px] border-[#e3e7f1] bg-white p-[16px] sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-wrap items-center gap-[6px]">
          {(
            [
              { id: 'all', label: '全部' },
              { id: 'active', label: '有效中' },
              { id: 'expiring_soon', label: '即将到期' },
              { id: 'expired', label: '已过期' },
              { id: 'revoked', label: '已作废' },
            ] as const
          ).map((tab) => (
            <button
              key={tab.id}
              type="button"
              onClick={() => setStatusFilter(tab.id)}
              className={cn(
                'rounded-[8px] px-[12px] py-[6px] text-[13px] font-medium transition-colors',
                statusFilter === tab.id
                  ? 'bg-[#18181a] text-white'
                  : 'text-[#5a6072] hover:bg-[#f4f6fa] hover:text-[#18181a]'
              )}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* Search */}
        <form onSubmit={handleSearchSubmit} className="flex min-w-[280px] items-center gap-[8px]">
          <div className="relative flex-1">
            <Search className="absolute left-[10px] top-[9px] size-[15px] text-[#9ca3af]" />
            <input
              type="text"
              placeholder="搜索报告名称、文件路径、创建人..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="h-[34px] w-full rounded-[10px] border-[0.5px] border-[#e3e7f1] bg-white pl-[32px] pr-[12px] text-[13px] outline-none focus:border-[#1a71ff]"
            />
          </div>
          <UIButton
            type="submit"
            size="sm"
            className="h-[34px] rounded-[10px] bg-[#18181a] px-[14px] text-[12px] text-white"
          >
            搜索
          </UIButton>
        </form>
      </div>

      {/* Links List / Table */}
      <div className="overflow-hidden rounded-[14px] border-[0.5px] border-[#e3e7f1] bg-white">
        {loading ? (
          <div className="flex h-[240px] items-center justify-center text-[13px] text-[#757f9c]">
            <RefreshCw className="mr-[8px] size-[16px] animate-spin" />
            加载链接数据中...
          </div>
        ) : links.length === 0 ? (
          <div className="flex h-[240px] flex-col items-center justify-center gap-[10px] text-center">
            <Link2 className="size-[36px] text-[#c0c6d4]" />
            <p className="text-[14px] font-medium text-[#464c5e]">暂无符合条件的分享链接</p>
            <p className="text-[12px] text-[#858b9c]">
              可以通过上方「+ 新建分享链接」为历史 HTML 报告生成安全外链
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-[13px]">
              <thead className="border-b border-[#f0f2f7] bg-[#f9fafc] text-[12px] font-medium text-[#757f9c]">
                <tr>
                  <th className="px-[18px] py-[12px]">报告名称与路径</th>
                  <th className="px-[14px] py-[12px]">状态</th>
                  <th className="px-[14px] py-[12px]">有效期 &amp; 到期时间</th>
                  <th className="px-[14px] py-[12px]">访问次数</th>
                  <th className="px-[14px] py-[12px]">创建信息</th>
                  <th className="px-[18px] py-[12px] text-right">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#f0f2f7]">
                {links.map((link) => {
                  const rem = formatRemaining(link);
                  return (
                    <tr key={link.id} className="transition-colors hover:bg-[#fafbfc]">
                      {/* Name & Path */}
                      <td className="max-w-[280px] px-[18px] py-[14px]">
                        <div className="flex items-start gap-[10px]">
                          <div className="mt-[2px] flex size-[28px] shrink-0 items-center justify-center rounded-[8px] bg-[#eff6ff] text-[#1a71ff]">
                            <FileCode className="size-[16px]" />
                          </div>
                          <div className="min-w-0">
                            <div className="truncate font-medium text-[#18181a]">
                              {link.title || 'HTML 报告'}
                            </div>
                            <div className="truncate text-[11px] text-[#858b9c]" title={link.path}>
                              {link.path}
                            </div>
                            {link.bound_task_id && (
                              <div className="mt-[3px] flex items-center gap-[4px]">
                                <span
                                  className="inline-flex items-center gap-[3px] rounded-[4px] bg-[#eff6ff] px-[5px] py-[1px] text-[10px] font-medium text-[#1a71ff] border border-[#bfdbfe]"
                                  title={`已绑定定时推送任务: ${link.bound_task_title || link.bound_task_id}`}
                                >
                                  <Clock className="size-[10px]" />
                                  定时推送: {link.bound_task_title || '已绑定'}
                                </span>
                              </div>
                            )}
                          </div>
                        </div>
                      </td>

                      {/* Status */}
                      <td className="px-[14px] py-[14px]">
                        {link.is_revoked ? (
                          <Badge variant="destructive" className="rounded-[6px] px-[8px] py-[2px] text-[11px]">
                            已作废
                          </Badge>
                        ) : link.status === 'expired' ? (
                          <Badge variant="secondary" className="rounded-[6px] bg-[#f3f4f6] text-[#6b7280]">
                            已过期
                          </Badge>
                        ) : link.status === 'expiring_soon' ? (
                          <Badge className="rounded-[6px] bg-[#fef3c7] text-[#92400e] hover:bg-[#fde68a]">
                            即将到期
                          </Badge>
                        ) : (
                          <Badge className="rounded-[6px] bg-[#ecfdf5] text-[#065f46] hover:bg-[#d1fae5]">
                            有效中
                          </Badge>
                        )}
                      </td>

                      {/* Expiration */}
                      <td className="px-[14px] py-[14px]">
                        <div className="flex flex-col">
                          <span
                            className={cn(
                              'font-medium text-[12px]',
                              rem.variant === 'destructive' && 'text-[#dc2626]',
                              rem.variant === 'warning' && 'text-[#d97706]',
                              rem.variant === 'secondary' && 'text-[#9ca3af]',
                              rem.variant === 'default' && 'text-[#059669]'
                            )}
                          >
                            {rem.text}
                          </span>
                          <span className="text-[11px] text-[#858b9c]">
                            {formatClientDateTime(link.expires_at)}
                          </span>
                        </div>
                      </td>

                      {/* Views */}
                      <td className="px-[14px] py-[14px]">
                        <div className="flex flex-col">
                          <span className="font-semibold text-[#18181a]">{link.view_count} 次</span>
                          {link.last_viewed_at && (
                            <span className="text-[11px] text-[#858b9c]">
                              最近: {formatClientDateTime(link.last_viewed_at)}
                            </span>
                          )}
                        </div>
                      </td>

                      {/* Creator */}
                      <td className="px-[14px] py-[14px]">
                        <div className="flex flex-col text-[12px]">
                          <span className="text-[#374151]">{link.created_by_username || '系统用户'}</span>
                          <span className="text-[11px] text-[#9ca3af]">
                            {formatClientDateTime(link.created_at)}
                          </span>
                        </div>
                      </td>

                      {/* Actions */}
                      <td className="px-[18px] py-[14px] text-right">
                        <div className="flex items-center justify-end gap-[6px]">
                          <button
                            type="button"
                            title="复制外链"
                            onClick={() => void handleCopy(link)}
                            className="flex size-[28px] items-center justify-center rounded-[6px] text-[#5a6072] hover:bg-[#f0f2f7] hover:text-[#18181a]"
                          >
                            <Copy className="size-[14px]" />
                          </button>
                          <button
                            type="button"
                            title="在新窗口打开预览"
                            onClick={() => handlePreview(link)}
                            className="flex size-[28px] items-center justify-center rounded-[6px] text-[#5a6072] hover:bg-[#f0f2f7] hover:text-[#18181a]"
                          >
                            <ExternalLink className="size-[14px]" />
                          </button>
                          <button
                            type="button"
                            title={link.bound_task_id ? `已绑定定时任务: ${link.bound_task_title || ''}（点击管理）` : "绑定到定时任务推送"}
                            onClick={() => void openBindModal(link)}
                            className={cn(
                              "flex size-[28px] items-center justify-center rounded-[6px] transition-all",
                              link.bound_task_id
                                ? "bg-[#eff6ff] text-[#1a71ff] hover:bg-[#dbeafe]"
                                : "text-[#5a6072] hover:bg-[#f0f2f7] hover:text-[#18181a]"
                            )}
                          >
                            <CalendarClock className="size-[14px]" />
                          </button>
                          <button
                            type="button"
                            title="编辑外链信息"
                            onClick={() => openEditModal(link)}
                            className="flex size-[28px] items-center justify-center rounded-[6px] text-[#5a6072] hover:bg-[#f0f2f7] hover:text-[#18181a]"
                          >
                            <Pencil className="size-[14px]" />
                          </button>
                          <button
                            type="button"
                            title="调整/延长有效期"
                            onClick={() => {
                              setSelectedLink(link);
                              setExtendDays('7');
                              setCustomExtendDays('');
                              setExtendModalOpen(true);
                            }}
                            className="flex size-[28px] items-center justify-center rounded-[6px] text-[#5a6072] hover:bg-[#f0f2f7] hover:text-[#18181a]"
                          >
                            <Clock className="size-[14px]" />
                          </button>
                          {!link.is_revoked && (
                            <button
                              type="button"
                              title="一键作废外链"
                              onClick={() => {
                                setSelectedLink(link);
                                setRevokeModalOpen(true);
                              }}
                              className="flex size-[28px] items-center justify-center rounded-[6px] text-[#dc2626] hover:bg-[#fee2e2]"
                            >
                              <ShieldAlert className="size-[14px]" />
                            </button>
                          )}
                          <button
                            type="button"
                            title="删除记录"
                            onClick={() => {
                              setSelectedLink(link);
                              setDeleteModalOpen(true);
                            }}
                            className="flex size-[28px] items-center justify-center rounded-[6px] text-[#9ca3af] hover:bg-[#fee2e2] hover:text-[#dc2626]"
                          >
                            <Trash2 className="size-[14px]" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Modal: Create Share Link (Semantic First, with Manual Fallback) */}
      <Dialog open={createModalOpen} onOpenChange={setCreateModalOpen}>
        <DialogContent className="w-[calc(100%-32px)] sm:max-w-[620px] max-h-[90vh] overflow-y-auto p-[24px]">
          <DialogHeader className="pr-[36px]">
            <DialogTitle className="flex items-center gap-[8px] text-[18px]">
              <Sparkles className="size-[20px] text-[#1a71ff]" />
              新建 HTML 报告分享链接
            </DialogTitle>
            <DialogDescription className="text-[13px] leading-[22px] text-[#757f9c]">
              支持 AI 语义智能规划并生成交互式在线报表，
              <br />
              或手动关联工作区已有静态 HTML 报告。
            </DialogDescription>
          </DialogHeader>

          {/* Prominent Tab Switcher */}
          <div className="grid grid-cols-2 rounded-[10px] bg-[#f1f3f7] p-[3px] gap-[4px] mt-[4px]">
            <button
              type="button"
              onClick={() => setCreationMode('semantic')}
              className={cn(
                'flex items-center justify-center gap-[6px] rounded-[7px] py-[7px] text-[13px] font-medium transition-all',
                creationMode === 'semantic'
                  ? 'bg-white text-[#1a71ff] shadow-sm font-semibold'
                  : 'text-[#6b7280] hover:text-[#18181a]'
              )}
            >
              <Sparkles className="size-[15px]" />
              AI 语义智能生成 (推荐)
            </button>
            <button
              type="button"
              onClick={() => setCreationMode('manual')}
              className={cn(
                'flex items-center justify-center gap-[6px] rounded-[7px] py-[7px] text-[13px] font-medium transition-all',
                creationMode === 'manual'
                  ? 'bg-white text-[#18181a] shadow-sm font-semibold'
                  : 'text-[#6b7280] hover:text-[#18181a]'
              )}
            >
              <FileCode className="size-[15px]" />
              手动关联已有报告
            </button>
          </div>

          {creationMode === 'semantic' ? (
            /* --- Semantic Creation Form --- */
            <div className="flex flex-col gap-[14px] py-[8px]">
              {/* Inspiration Chips */}
              <div className="flex flex-col gap-[6px]">
                <div className="flex items-center justify-between">
                  <span className="text-[12px] font-medium text-[#757f9c]">💡 推荐灵感（点击快速填入）：</span>
                </div>
                <div className="flex flex-wrap gap-[6px]">
                  {INSPIRATION_PROMPTS.map((insp, idx) => (
                    <button
                      key={idx}
                      type="button"
                      onClick={() => {
                        setSemanticForm((prev) => ({
                          ...prev,
                          prompt: insp.prompt,
                          title: insp.title,
                          renderer: insp.renderer,
                        }));
                      }}
                      className="rounded-[6px] border border-[#e3e7f1] bg-[#f9fafc] px-[8px] py-[4px] text-[12px] text-[#4b5563] transition-all hover:border-[#1a71ff] hover:bg-[#eff6ff] hover:text-[#1a71ff]"
                    >
                      {insp.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Prompt Textarea */}
              <div className="flex flex-col gap-[6px]">
                <label className="text-[13px] font-medium text-[#374151]">
                  报表语义需求描述 <span className="text-[#dc2626]">*</span>
                </label>
                <Textarea
                  rows={4}
                  placeholder="例如：分析最近一个季度的销售业绩，包含按地区和品类的销售额、目标达成率与环比趋势，并生成核心 KPI 与达成率表格..."
                  value={semanticForm.prompt}
                  onChange={(e) => setSemanticForm({ ...semanticForm, prompt: e.target.value })}
                  className="resize-none text-[13px] leading-[20px]"
                />
              </div>

              {/* Title & Template style */}
              <div className="grid grid-cols-1 gap-[12px] sm:grid-cols-2">
                <div className="flex flex-col gap-[6px]">
                  <label className="text-[13px] font-medium text-[#374151]">报表主标题（可选）</label>
                  <Input
                    placeholder="留空则由 AI 自动推断"
                    value={semanticForm.title}
                    onChange={(e) => setSemanticForm({ ...semanticForm, title: e.target.value })}
                    className="h-[36px] text-[13px]"
                  />
                </div>
                <div className="flex flex-col gap-[6px]">
                  <label className="text-[13px] font-medium text-[#374151]">展现模板样式</label>
                  <Select
                    value={semanticForm.renderer}
                    onValueChange={(val: 'sales_report' | 'generic_table') =>
                      setSemanticForm({ ...semanticForm, renderer: val })
                    }
                  >
                    <SelectTrigger className="h-[36px] text-[13px]">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="sales_report">📊 综合运营看板 (含 KPI + 卡片)</SelectItem>
                      <SelectItem value="generic_table">📋 交互式数据明细表格</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>

              {/* Validity TTL settings */}
              <div className="flex flex-col gap-[8px]">
                <label className="text-[13px] font-medium text-[#374151]">有效期限（TTL）</label>
                <div className="grid grid-cols-3 gap-[8px] sm:grid-cols-6">
                  {[
                    { value: '1', label: '1 天' },
                    { value: '3', label: '3 天' },
                    { value: '7', label: '7 天 (默认)' },
                    { value: '14', label: '14 天' },
                    { value: '30', label: '30 天' },
                    { value: 'custom', label: '自定义' },
                  ].map((opt) => (
                    <button
                      key={opt.value}
                      type="button"
                      onClick={() => setSemanticForm({ ...semanticForm, days: opt.value })}
                      className={cn(
                        'rounded-[8px] border py-[6px] text-center text-[12px] font-medium transition-all',
                        semanticForm.days === opt.value
                          ? 'border-[#1a71ff] bg-[#eff6ff] text-[#1a71ff]'
                          : 'border-[#e3e7f1] bg-white text-[#4b5563] hover:border-[#cbd3e6]'
                      )}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>

                {semanticForm.days === 'custom' && (
                  <div className="mt-[4px] flex items-center gap-[8px]">
                    <Input
                      type="number"
                      min="1"
                      max="365"
                      placeholder="输入有效天数，如 90"
                      value={semanticForm.custom_days}
                      onChange={(e) => setSemanticForm({ ...semanticForm, custom_days: e.target.value })}
                      className="h-[34px] w-[160px] text-[13px]"
                    />
                    <span className="text-[13px] text-[#757f9c]">天</span>
                  </div>
                )}
              </div>

              {/* Scheduled push config card */}
              <div className="rounded-[10px] border border-[#e3e7f1] bg-[#fbfcfd] p-[12px] flex flex-col gap-[10px]">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-[8px]">
                    <Clock className="size-[16px] text-[#1a71ff]" />
                    <div>
                      <span className="text-[13px] font-medium text-[#18181a]">同时创建定时任务推送</span>
                      <p className="text-[11px] text-[#757f9c]">生成外链后，自动注册定时任务周期性推送到飞书群/机器人</p>
                    </div>
                  </div>
                  <Switch
                    checked={scheduledPush.enabled}
                    onCheckedChange={(checked) => setScheduledPush((prev) => ({ ...prev, enabled: checked }))}
                  />
                </div>

                {scheduledPush.enabled && (
                  <div className="flex flex-col gap-[10px] pt-[8px] border-t border-[#f0f2f5]">
                    <div className="grid grid-cols-2 gap-[10px]">
                      <div className="flex flex-col gap-[4px]">
                        <label className="text-[12px] text-[#4b5563]">推送频率</label>
                        <Select
                          value={scheduledPush.schedule_type}
                          onValueChange={(val: 'daily' | 'interval') => setScheduledPush((prev) => ({ ...prev, schedule_type: val }))}
                        >
                          <SelectTrigger className="h-[34px] text-[12px]">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="daily">每天定点推送</SelectItem>
                            <SelectItem value="interval">固定间隔轮询</SelectItem>
                          </SelectContent>
                        </Select>
                      </div>
                      <div className="flex flex-col gap-[4px]">
                        <label className="text-[12px] text-[#4b5563]">
                          {scheduledPush.schedule_type === 'daily' ? '每日推送时刻' : '间隔分钟数'}
                        </label>
                        {scheduledPush.schedule_type === 'daily' ? (
                          <Input
                            type="time"
                            value={scheduledPush.time}
                            onChange={(e) => setScheduledPush((prev) => ({ ...prev, time: e.target.value }))}
                            className="h-[34px] text-[12px]"
                          />
                        ) : (
                          <Input
                            type="number"
                            min="5"
                            max="1440"
                            placeholder="例如：60"
                            value={scheduledPush.interval_minutes}
                            onChange={(e) => setScheduledPush((prev) => ({ ...prev, interval_minutes: parseInt(e.target.value, 10) || 60 }))}
                            className="h-[34px] text-[12px]"
                          />
                        )}
                      </div>
                    </div>

                    <div className="flex flex-col gap-[4px]">
                      <label className="text-[12px] text-[#4b5563]">飞书群 Webhook 机器人地址（可选）</label>
                      <Input
                        placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..."
                        value={scheduledPush.feishu_webhook}
                        onChange={(e) => setScheduledPush((prev) => ({ ...prev, feishu_webhook: e.target.value }))}
                        className="h-[34px] text-[12px]"
                      />
                    </div>
                  </div>
                )}
              </div>
            </div>
          ) : (
            /* --- Manual Creation Form --- */
            <div className="flex flex-col gap-[14px] py-[8px]">
              {/* Quick picker from recent reports */}
              {recentReports.length > 0 && (
                <div className="flex flex-col gap-[6px]">
                  <label className="text-[13px] font-medium text-[#374151]">
                    快速选择最近报告
                  </label>
                  <Select value={selectedReportKey} onValueChange={handleReportSelect}>
                    <SelectTrigger className="h-[36px] w-full text-[13px]">
                      <SelectValue placeholder="从最近生成的 HTML 报告中选择" className="truncate" />
                    </SelectTrigger>
                    <SelectContent className="max-w-[560px]">
                      {recentReports.map((r, i) => (
                        <SelectItem key={i} value={String(i)}>
                          <span className="truncate max-w-[480px] inline-block">{r.display_name} ({r.path})</span>
                        </SelectItem>
                      ))}
                      <SelectItem value="manual">手动填写会话与路径...</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              )}

              {/* Display title */}
              <div className="flex flex-col gap-[6px]">
                <label className="text-[13px] font-medium text-[#374151]">报告标题 / 备注</label>
                <Input
                  placeholder="例如：2026年Q3销售运营报表"
                  value={createForm.title}
                  onChange={(e) => setCreateForm({ ...createForm, title: e.target.value })}
                  className="h-[36px] text-[13px]"
                />
              </div>

              {/* Session ID & Path */}
              <div className="grid grid-cols-2 gap-[12px]">
                <div className="flex flex-col gap-[6px]">
                  <label className="text-[13px] font-medium text-[#374151]">会话 ID</label>
                  <Input
                    placeholder="session_xxx"
                    value={createForm.session_id}
                    onChange={(e) => setCreateForm({ ...createForm, session_id: e.target.value })}
                    className="h-[36px] text-[13px]"
                  />
                </div>
                <div className="flex flex-col gap-[6px]">
                  <label className="text-[13px] font-medium text-[#374151]">报告相对路径</label>
                  <Input
                    placeholder="reports/xxx.html"
                    value={createForm.path}
                    onChange={(e) => setCreateForm({ ...createForm, path: e.target.value })}
                    className="h-[36px] text-[13px]"
                  />
                </div>
              </div>

              {/* Validity TTL settings */}
              <div className="flex flex-col gap-[8px]">
                <label className="text-[13px] font-medium text-[#374151]">有效期限（TTL）</label>
                <div className="grid grid-cols-3 gap-[8px] sm:grid-cols-6">
                  {[
                    { value: '1', label: '1 天' },
                    { value: '3', label: '3 天' },
                    { value: '7', label: '7 天 (默认)' },
                    { value: '14', label: '14 天' },
                    { value: '30', label: '30 天' },
                    { value: 'custom', label: '自定义' },
                  ].map((opt) => (
                    <button
                      key={opt.value}
                      type="button"
                      onClick={() => setCreateForm({ ...createForm, days: opt.value })}
                      className={cn(
                        'rounded-[8px] border py-[6px] text-center text-[12px] font-medium transition-all',
                        createForm.days === opt.value
                          ? 'border-[#1a71ff] bg-[#eff6ff] text-[#1a71ff]'
                          : 'border-[#e3e7f1] bg-white text-[#4b5563] hover:border-[#cbd3e6]'
                      )}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>

                {createForm.days === 'custom' && (
                  <div className="mt-[4px] flex items-center gap-[8px]">
                    <Input
                      type="number"
                      min="1"
                      max="365"
                      placeholder="输入有效天数，如 90"
                      value={createForm.custom_days}
                      onChange={(e) => setCreateForm({ ...createForm, custom_days: e.target.value })}
                      className="h-[34px] w-[160px] text-[13px]"
                    />
                    <span className="text-[13px] text-[#757f9c]">天</span>
                  </div>
                )}
              </div>

              {/* Scheduled push config card */}
              <div className="rounded-[10px] border border-[#e3e7f1] bg-[#fbfcfd] p-[12px] flex flex-col gap-[10px]">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-[8px]">
                    <Clock className="size-[16px] text-[#1a71ff]" />
                    <div>
                      <span className="text-[13px] font-medium text-[#18181a]">同时创建定时任务推送</span>
                      <p className="text-[11px] text-[#757f9c]">生成外链后，自动注册定时任务周期性推送到飞书群/机器人</p>
                    </div>
                  </div>
                  <Switch
                    checked={scheduledPush.enabled}
                    onCheckedChange={(checked) => setScheduledPush((prev) => ({ ...prev, enabled: checked }))}
                  />
                </div>

                {scheduledPush.enabled && (
                  <div className="flex flex-col gap-[10px] pt-[8px] border-t border-[#f0f2f5]">
                    <div className="grid grid-cols-2 gap-[10px]">
                      <div className="flex flex-col gap-[4px]">
                        <label className="text-[12px] text-[#4b5563]">推送频率</label>
                        <Select
                          value={scheduledPush.schedule_type}
                          onValueChange={(val: 'daily' | 'interval') => setScheduledPush((prev) => ({ ...prev, schedule_type: val }))}
                        >
                          <SelectTrigger className="h-[34px] text-[12px]">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="daily">每天定点推送</SelectItem>
                            <SelectItem value="interval">固定间隔轮询</SelectItem>
                          </SelectContent>
                        </Select>
                      </div>
                      <div className="flex flex-col gap-[4px]">
                        <label className="text-[12px] text-[#4b5563]">
                          {scheduledPush.schedule_type === 'daily' ? '每日推送时刻' : '间隔分钟数'}
                        </label>
                        {scheduledPush.schedule_type === 'daily' ? (
                          <Input
                            type="time"
                            value={scheduledPush.time}
                            onChange={(e) => setScheduledPush((prev) => ({ ...prev, time: e.target.value }))}
                            className="h-[34px] text-[12px]"
                          />
                        ) : (
                          <Input
                            type="number"
                            min="5"
                            max="1440"
                            placeholder="例如：60"
                            value={scheduledPush.interval_minutes}
                            onChange={(e) => setScheduledPush((prev) => ({ ...prev, interval_minutes: parseInt(e.target.value, 10) || 60 }))}
                            className="h-[34px] text-[12px]"
                          />
                        )}
                      </div>
                    </div>

                    <div className="flex flex-col gap-[4px]">
                      <label className="text-[12px] text-[#4b5563]">飞书群 Webhook 机器人地址（可选）</label>
                      <Input
                        placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..."
                        value={scheduledPush.feishu_webhook}
                        onChange={(e) => setScheduledPush((prev) => ({ ...prev, feishu_webhook: e.target.value }))}
                        className="h-[34px] text-[12px]"
                      />
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          <DialogFooter className="mt-[10px]">
            <UIButton
              type="button"
              variant="outline"
              size="sm"
              disabled={generating}
              onClick={() => setCreateModalOpen(false)}
              className={DIALOG_CANCEL_BUTTON_CLASS}
            >
              取消
            </UIButton>
            {creationMode === 'semantic' ? (
              <UIButton
                type="button"
                size="sm"
                disabled={generating}
                onClick={() => void handleSemanticGenerateSubmit()}
                className={DIALOG_PRIMARY_BUTTON_CLASS}
              >
                {generating ? (
                  <>
                    <RefreshCw className="mr-[6px] size-[14px] animate-spin" />
                    正在智能规划与生成 HTML...
                  </>
                ) : (
                  <>
                    <Sparkles className="mr-[6px] size-[14px]" />
                    一键语义生成并复制外链
                  </>
                )}
              </UIButton>
            ) : (
              <UIButton
                type="button"
                size="sm"
                onClick={() => void handleCreateSubmit()}
                className={DIALOG_PRIMARY_BUTTON_CLASS}
              >
                生成并复制外链
              </UIButton>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Modal: Bind to Scheduled Task */}
      <Dialog open={bindModalOpen} onOpenChange={setBindModalOpen}>
        <DialogContent className="w-[calc(100%-32px)] sm:max-w-[540px] max-h-[90vh] overflow-hidden p-[24px]">
          <DialogHeader className="pr-[36px] min-w-0">
            <DialogTitle className="flex items-center gap-[8px] text-[17px]">
              <CalendarClock className="size-[18px] text-[#1a71ff] shrink-0" />
              绑定 HTML 报告至定时推送任务
            </DialogTitle>
            <DialogDescription className="text-[13px] leading-[22px] text-[#757f9c]">
              将当前报告外链挂载到定时任务中，
              <br />
              支持周期性自动将交互式报告推送到飞书群聊或机器人。
            </DialogDescription>
          </DialogHeader>

          <div className="flex flex-col gap-[14px] py-[8px] min-w-0 overflow-y-auto">
            {/* Current report summary card */}
            <div className="rounded-[10px] bg-[#f8fafc] border border-[#e2e8f0] p-[12px] flex items-start gap-[10px]">
              <FileCode className="size-[18px] text-[#1a71ff] mt-[2px] shrink-0" />
              <div className="min-w-0 flex-1 text-[12px]">
                <div className="font-semibold text-[#18181a] truncate">
                  {selectedLinkForBinding?.title || 'HTML 业务报告'}
                </div>
                <div className="text-[#64748b] truncate mt-[2px] font-mono text-[11px]">
                  {selectedLinkForBinding?.url}
                </div>
                {selectedLinkForBinding?.bound_task_title && (
                  <div className="mt-[4px] font-medium text-[#2563eb]">
                    当前已绑定：{selectedLinkForBinding.bound_task_title}
                  </div>
                )}
              </div>
            </div>

            {/* Bind Mode Switcher */}
            <div className="grid grid-cols-2 rounded-[8px] bg-[#f1f3f7] p-[3px] gap-[4px]">
              <button
                type="button"
                onClick={() => setBindMode('new')}
                className={cn(
                  'flex items-center justify-center gap-[6px] rounded-[6px] py-[6px] text-[12px] font-medium transition-all',
                  bindMode === 'new'
                    ? 'bg-white text-[#1a71ff] shadow-sm font-semibold'
                    : 'text-[#6b7280] hover:text-[#18181a]'
                )}
              >
                <Plus className="size-[13px]" />
                新建定时推送任务
              </button>
              <button
                type="button"
                onClick={() => setBindMode('existing')}
                className={cn(
                  'flex items-center justify-center gap-[6px] rounded-[6px] py-[6px] text-[12px] font-medium transition-all',
                  bindMode === 'existing'
                    ? 'bg-white text-[#18181a] shadow-sm font-semibold'
                    : 'text-[#6b7280] hover:text-[#18181a]'
                )}
              >
                <Clock className="size-[13px]" />
                绑定至已有定时任务
              </button>
            </div>

            {bindMode === 'existing' ? (
              <div className="flex flex-col gap-[12px] min-w-0">
                <div className="flex flex-col gap-[6px]">
                  <label className="text-[13px] font-medium text-[#374151]">
                    选择目标定时任务 <span className="text-[#dc2626]">*</span>
                  </label>
                  {loadingExistingTasks ? (
                    <div className="text-[12px] text-[#9ca3af] py-[8px]">加载定时任务列表中...</div>
                  ) : existingTasks.length === 0 ? (
                    <div className="text-[12px] text-[#d97706] py-[8px]">暂无已有定时任务，请选择“新建定时推送任务”</div>
                  ) : (
                    <Select
                      value={bindForm.selectedTaskId}
                      onValueChange={(val) => setBindForm({ ...bindForm, selectedTaskId: val })}
                    >
                      <SelectTrigger className="h-[36px] text-[13px]">
                        <SelectValue placeholder="请选择定时任务..." />
                      </SelectTrigger>
                      <SelectContent>
                        {existingTasks.map((t) => (
                          <SelectItem key={t.id} value={t.id}>
                            {t.title} ({t.schedule_type === 'daily' ? '每日定点' : t.schedule_type})
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  )}
                </div>

                <div className="flex flex-col gap-[6px]">
                  <label className="text-[13px] font-medium text-[#374151]">补充飞书群 Webhook 机器人（可选）</label>
                  <Input
                    placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..."
                    value={bindForm.feishu_webhook}
                    onChange={(e) => setBindForm({ ...bindForm, feishu_webhook: e.target.value })}
                    className="h-[36px] text-[13px]"
                  />
                </div>
              </div>
            ) : (
              <div className="flex flex-col gap-[12px] min-w-0">
                <div className="flex flex-col gap-[6px]">
                  <label className="text-[13px] font-medium text-[#374151]">任务名称</label>
                  <Input
                    value={bindForm.task_title}
                    onChange={(e) => setBindForm({ ...bindForm, task_title: e.target.value })}
                    className="h-[36px] text-[13px]"
                  />
                </div>

                <div className="grid grid-cols-2 gap-[12px]">
                  <div className="flex flex-col gap-[6px]">
                    <label className="text-[13px] font-medium text-[#374151]">调度类型</label>
                    <Select
                      value={bindForm.schedule_type}
                      onValueChange={(val: 'daily' | 'interval') => setBindForm({ ...bindForm, schedule_type: val })}
                    >
                      <SelectTrigger className="h-[36px] text-[13px]">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="daily">每天定点推送</SelectItem>
                        <SelectItem value="interval">固定间隔轮询</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="flex flex-col gap-[6px]">
                    <label className="text-[13px] font-medium text-[#374151]">
                      {bindForm.schedule_type === 'daily' ? '每日推送时刻' : '间隔分钟数'}
                    </label>
                    {bindForm.schedule_type === 'daily' ? (
                      <Input
                        type="time"
                        value={bindForm.time}
                        onChange={(e) => setBindForm({ ...bindForm, time: e.target.value })}
                        className="h-[36px] text-[13px]"
                      />
                    ) : (
                      <Input
                        type="number"
                        min="5"
                        max="1440"
                        value={bindForm.interval_minutes}
                        onChange={(e) => setBindForm({ ...bindForm, interval_minutes: parseInt(e.target.value, 10) || 60 })}
                        className="h-[36px] text-[13px]"
                      />
                    )}
                  </div>
                </div>

                <div className="flex flex-col gap-[6px]">
                  <label className="text-[13px] font-medium text-[#374151]">飞书群 Webhook 机器人地址（可选）</label>
                  <Input
                    placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..."
                    value={bindForm.feishu_webhook}
                    onChange={(e) => setBindForm({ ...bindForm, feishu_webhook: e.target.value })}
                    className="h-[36px] text-[13px]"
                  />
                </div>
              </div>
            )}
          </div>

          <DialogFooter className="mt-[10px]">
            <UIButton
              type="button"
              variant="outline"
              size="sm"
              disabled={bindSubmitting}
              onClick={() => setBindModalOpen(false)}
              className={DIALOG_CANCEL_BUTTON_CLASS}
            >
              取消
            </UIButton>
            <UIButton
              type="button"
              size="sm"
              disabled={bindSubmitting}
              onClick={() => void handleBindSubmit()}
              className={DIALOG_PRIMARY_BUTTON_CLASS}
            >
              {bindSubmitting ? '正在绑定...' : bindMode === 'existing' ? '确认绑定' : '创建并绑定推送'}
            </UIButton>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Edit Share Link Modal */}
      <Dialog open={editModalOpen} onOpenChange={setEditModalOpen}>
        <DialogContent className="w-[calc(100%-32px)] sm:max-w-[480px] max-w-[95vw] p-[24px] min-w-0 overflow-hidden">
          <DialogHeader className="min-w-0">
            <DialogTitle className="flex items-center gap-[8px] text-[16px]">
              <Pencil className="size-[18px] text-[#1a71ff]" />
              编辑分享链接
            </DialogTitle>
            <DialogDescription className="text-[13px] text-[#757f9c]">
              修改外链报告名称或有效时限。修改有效期会重新签发链接，旧链接将立即失效。
            </DialogDescription>
          </DialogHeader>

          <div className="flex flex-col gap-[14px] py-[12px] min-w-0 max-w-full">
            {/* Readonly info */}
            <div className="rounded-[10px] bg-[#f9fafc] p-[12px] text-[12px] text-[#5a6072] flex flex-col gap-[6px] min-w-0 max-w-full overflow-hidden">
              <div className="break-all leading-[18px]">
                报告路径：<code className="text-[#18181a] font-mono break-all">{selectedLinkForEdit?.path}</code>
              </div>
              <div className="leading-[18px]">
                当前状态：<span className="font-medium text-[#18181a]">{selectedLinkForEdit?.is_revoked ? '已作废' : (selectedLinkForEdit?.remaining_seconds ?? 0) <= 0 ? '已过期' : '正常'}</span>
              </div>
              <div className="leading-[18px]">
                当前到期时间：{formatClientDateTime(selectedLinkForEdit?.expires_at)}
              </div>
              {selectedLinkForEdit?.bound_task_title && (
                <div className="break-words leading-[18px]">
                  绑定任务：<span className="text-[#1a71ff]">{selectedLinkForEdit.bound_task_title}</span>
                </div>
              )}
              <div className="min-w-0 flex flex-col gap-[2px]">
                <span className="text-[#5a6072]">当前 URL：</span>
                <div className="break-all font-mono text-[11px] text-[#64748b] bg-white border border-[#e2e8f0] rounded-[6px] p-[6px] max-h-[72px] overflow-y-auto select-all leading-[16px]">
                  {selectedLinkForEdit?.url}
                </div>
              </div>
            </div>

            {/* Title field */}
            <div className="flex flex-col gap-[6px]">
              <label htmlFor="share-link-title" className="text-[13px] font-medium text-[#374151]">
                报告名称
              </label>
              <Input
                id="share-link-title"
                value={editForm.title}
                maxLength={200}
                onChange={(e) => setEditForm((prev) => ({ ...prev, title: e.target.value }))}
                placeholder="请输入报告名称"
              />
              {(selectedLinkForEdit?.title || '').startsWith('【演示数据】') && (
                <div className="text-[12px] text-[#eab308]">
                  演示数据链接的名称必须保留【演示数据】前缀。
                </div>
              )}
            </div>

            {/* Expiry field */}
            <div className="flex flex-col gap-[6px]">
              <div className="flex items-center justify-between">
                <label htmlFor="share-link-expires-at" className="text-[13px] font-medium text-[#374151]">
                  到期时间
                </label>
                <span className="text-[11px] text-[#9ca3af]">时区: {getClientTimeZone()}</span>
              </div>
              <Input
                id="share-link-expires-at"
                type="datetime-local"
                value={editForm.expiresAt}
                disabled={Boolean(selectedLinkForEdit?.is_revoked)}
                onChange={(e) => setEditForm((prev) => ({ ...prev, expiresAt: e.target.value }))}
              />
              {selectedLinkForEdit?.is_revoked ? (
                <div className="text-[12px] text-[#ef4444]">
                  已作废的链接不能修改有效期，请重新生成链接。
                </div>
              ) : (selectedLinkForEdit?.remaining_seconds ?? 0) <= 0 ? (
                <div className="text-[12px] text-[#3b82f6]">
                  该链接已过期，设置未来时间可恢复访问。
                </div>
              ) : (
                <div className="text-[11px] text-[#9ca3af]">
                  修改有效期会重新签发链接，旧链接将立即失效。
                </div>
              )}
            </div>

            {/* Inline error */}
            {editError && (
              <div className="flex items-center gap-[6px] rounded-[8px] bg-[#fef2f2] p-[10px] text-[12px] text-[#dc2626]">
                <AlertCircle className="size-[14px] shrink-0" />
                <span>{editError}</span>
              </div>
            )}
          </div>

          <DialogFooter className="mt-[10px]">
            <UIButton
              variant="outline"
              onClick={() => setEditModalOpen(false)}
              disabled={editSubmitting}
            >
              取消
            </UIButton>
            <UIButton
              onClick={() => void handleEditSubmit()}
              disabled={editSubmitting}
            >
              {editSubmitting ? '保存中...' : '保存'}
            </UIButton>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Modal: Extend / Modify Validity */}
      <Dialog open={extendModalOpen} onOpenChange={setExtendModalOpen}>
        <DialogContent className="w-[calc(100%-32px)] sm:max-w-[460px] p-[24px]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-[8px] text-[16px]">
              <Clock className="size-[18px] text-[#1a71ff]" />
              调整报告外链有效期
            </DialogTitle>
            <DialogDescription className="text-[13px] text-[#757f9c]">
              重新签发有效时限。已作废的链接不能延长，请重新生成链接。
            </DialogDescription>
          </DialogHeader>

          <div className="flex flex-col gap-[14px] py-[12px]">
            <div className="rounded-[10px] bg-[#f9fafc] p-[12px] text-[12px] text-[#5a6072]">
              <div>当前到期时间：{formatClientDateTime(selectedLink?.expires_at)}</div>
              <div className="mt-[4px]">当前状态：{selectedLink?.is_revoked ? '已手动作废' : '正常'}</div>
            </div>

            <div className="flex flex-col gap-[8px]">
              <label className="text-[13px] font-medium text-[#374151]">延长时限</label>
              <div className="grid grid-cols-4 gap-[8px]">
                {[
                  { value: '1', label: '+1 天' },
                  { value: '3', label: '+3 天' },
                  { value: '7', label: '+7 天 (推荐)' },
                  { value: '30', label: '+30 天' },
                ].map((opt) => (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={() => setExtendDays(opt.value)}
                    className={cn(
                      'rounded-[8px] border py-[6px] text-center text-[12px] font-medium transition-all',
                      extendDays === opt.value
                        ? 'border-[#1a71ff] bg-[#eff6ff] text-[#1a71ff]'
                        : 'border-[#e3e7f1] bg-white text-[#4b5563] hover:border-[#cbd3e6]'
                    )}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <DialogFooter className="mt-[10px]">
            <UIButton
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setExtendModalOpen(false)}
              className={DIALOG_CANCEL_BUTTON_CLASS}
            >
              取消
            </UIButton>
            <UIButton
              type="button"
              size="sm"
              onClick={() => void handleExtendSubmit()}
              className={DIALOG_PRIMARY_BUTTON_CLASS}
            >
              确认延长
            </UIButton>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Modal: Revoke Confirmation */}
      <Dialog open={revokeModalOpen} onOpenChange={setRevokeModalOpen}>
        <DialogContent className="w-[calc(100%-32px)] sm:max-w-[420px] p-[24px]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-[8px] text-[16px] text-[#dc2626]">
              <ShieldAlert className="size-[18px]" />
              作废报告外链
            </DialogTitle>
            <DialogDescription className="text-[13px] text-[#5a6072]">
              确定要作废此外链吗？作废后，任何持有该链接的外部访问者均无法再查看报告内容。
            </DialogDescription>
          </DialogHeader>

          <DialogFooter className="mt-[16px]">
            <UIButton
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setRevokeModalOpen(false)}
              className={DIALOG_CANCEL_BUTTON_CLASS}
            >
              取消
            </UIButton>
            <UIButton
              type="button"
              size="sm"
              onClick={() => void handleRevokeSubmit()}
              className="h-[32px] min-w-[80px] rounded-[10px] bg-[#dc2626] px-[12px] text-[14px] font-normal text-white hover:bg-[#b91c1c]"
            >
              确认作废
            </UIButton>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Modal: Delete Confirmation */}
      <Dialog open={deleteModalOpen} onOpenChange={setDeleteModalOpen}>
        <DialogContent className="w-[calc(100%-32px)] sm:max-w-[400px] p-[24px]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-[8px] text-[16px] text-[#374151]">
              <Trash2 className="size-[18px] text-[#dc2626]" />
              删除外链记录
            </DialogTitle>
            <DialogDescription className="text-[13px] text-[#757f9c]">
              确定要从列表中删除这条外链管理记录吗？
            </DialogDescription>
          </DialogHeader>

          <DialogFooter className="mt-[16px]">
            <UIButton
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setDeleteModalOpen(false)}
              className={DIALOG_CANCEL_BUTTON_CLASS}
            >
              取消
            </UIButton>
            <UIButton
              type="button"
              size="sm"
              onClick={() => void handleDeleteSubmit()}
              className="h-[32px] min-w-[80px] rounded-[10px] bg-[#dc2626] px-[12px] text-[14px] font-normal text-white hover:bg-[#b91c1c]"
            >
              删除
            </UIButton>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
