import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { notify } from '@/components/ui/app-toast';
import { getClientTimeZone } from '@/lib/timezone';

import AppHeader from '@/components/AppHeader';
import { Button } from '@/components/ui/button';
import {
  Checkbox,
  Input,
  Label,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Switch,
  Textarea,
} from '@/components/ui';
import { cn } from '@/lib/utils';
import {
  AlertCircle,
  AlertTriangle,
  Bell,
  Bot,
  CheckCircle2,
  Copy,
  Link2,
  Plus,
  RotateCw,
  Send,
  Smartphone,
  Trash2,
  User,
  UserCheck,
  Users,
  X,
} from 'lucide-react';

import SearchableSelect, { type SearchableSelectOption } from '@/components/SearchableSelect';

import { api, TENANT_ID } from '../../api/client';
import IconArrowRight from '../../assets/icons/arrow-right.svg?react';
import IconAlarm from '../../assets/icons/profile-alarm.svg?react';
import type { EnterpriseAuthUser } from '../../auth';
import { EnterpriseRoute } from '../../enums/routes';
import { isTeamScope, readEmployeeScope } from '../../lib/agent-scope-storage';
import type { ScheduledTaskRead, SkillRead } from '../../types';
import {
  INITIAL_VALUES,
  WEEKDAY_OPTIONS,
  buildSchedule,
  scheduledTaskSopOptions,
  switchFeishuApp,
  taskToFormValues,
  type FeishuAppRead,
  type TaskFormValues,
} from './shared';

export type ScheduledTaskPageProps = {
  currentUser?: EnterpriseAuthUser;
  onLogout?: () => void;
};

export function ScheduledTaskNewPage(props: ScheduledTaskPageProps = {}) {
  return <ScheduledTaskEditorPage mode="new" {...props} />;
}

export function ScheduledTaskEditPage(props: ScheduledTaskPageProps = {}) {
  return <ScheduledTaskEditorPage mode="edit" {...props} />;
}

type FormErrors = Partial<Record<'title' | 'prompt' | 'run_at' | 'time' | 'weekdays' | 'interval_minutes', string>>;

type FeishuChat = {
  chat_id: string;
  name: string;
  avatar?: string;
  description?: string;
};

const CARD_CLASS =
  'rounded-[14px] border border-[#eceef1] bg-white p-[20px]';
const CARD_TITLE_CLASS = 'mb-[16px] text-[14px] font-medium text-[#18181a]';
const FIELD_LABEL_CLASS = 'text-[13px] font-medium text-[#18181a]';
const FIELD_ERROR_CLASS = 'text-[12px] leading-none text-[#d20b0b]';

function feishuAppLabel(app: FeishuAppRead): string {
  return app.name || app.bot_name || app.app_id || app.id;
}

function ScheduledTaskEditorPage({
  mode,
  currentUser,
  onLogout,
}: { mode: 'new' | 'edit' } & ScheduledTaskPageProps) {
  const [values, setValues] = useState<TaskFormValues>(INITIAL_VALUES);
  const [errors, setErrors] = useState<FormErrors>({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [agentId, setAgentId] = useState(readEmployeeScope);
  const [sops, setSops] = useState<SkillRead[]>([]);
  const [taskMetadata, setTaskMetadata] = useState<Record<string, unknown>>({});
  const [feishuChats, setFeishuChats] = useState<FeishuChat[]>([]);
  const [loadingChats, setLoadingChats] = useState(false);
  const [chatsError, setChatsError] = useState('');
  const [feishuApps, setFeishuApps] = useState<FeishuAppRead[]>([]);
  const [loadingApps, setLoadingApps] = useState(false);
  const [appsError, setAppsError] = useState('');
  const [mobileInput, setMobileInput] = useState('');
  const [webhookInput, setWebhookInput] = useState('');
  const [newTimeInput, setNewTimeInput] = useState('13:00');

  const dailyTimes = values.times && values.times.length > 0 ? values.times : [values.time || '09:00'];

  function setDailyTimes(nextTimes: string[]) {
    const sorted = Array.from(new Set(nextTimes.filter(Boolean))).sort();
    const finalTimes = sorted.length > 0 ? sorted : ['09:00'];
    setValues((prev) => ({
      ...prev,
      times: finalTimes,
      time: finalTimes[0],
    }));
  }

  function addDailyTime(timeToAdd?: string) {
    const t = (timeToAdd || newTimeInput).trim();
    if (!t) return;
    if (dailyTimes.includes(t)) {
      notify.error(`时段 ${t} 已在列表中`);
      return;
    }
    setDailyTimes([...dailyTimes, t]);
  }

  function removeDailyTime(t: string) {
    if (dailyTimes.length <= 1) {
      notify.error('请至少保留一个执行时段');
      return;
    }
    setDailyTimes(dailyTimes.filter((item) => item !== t));
  }

  function applyTimePreset(preset: string[]) {
    setDailyTimes(preset);
  }
  const [chatsReloadToken, setChatsReloadToken] = useState(0);
  const [testingNotify, setTestingNotify] = useState(false);
  const [testResult, setTestResult] = useState<{
    ok: boolean;
    sent_count: number;
    failed_count: number;
    sent: Array<{ target_type: string; identifier: string }>;
    failed: Array<{ target_type: string; identifier: string; error: string }>;
    error?: string;
  } | null>(null);
  const [feishuRecipients, setFeishuRecipients] = useState<
    Array<{ open_id: string; display_name: string; user_id: string }>
  >([]);
  const navigate = useNavigate();
  const { taskId } = useParams();
  const isEdit = mode === 'edit';
  const scheduleType = values.schedule_type;
  const selectedBindingId = values.feishu_notify.binding_id || '';

  const activeFeishuApps = feishuApps.filter((app) => app.status === 'active');
  const noUsableApp = !loadingApps && !appsError && activeFeishuApps.length === 0;
  // 任务记录的应用已停用/删除，或本就不在本租户的飞书绑定里。列表未就绪时不下结论。
  const boundAppUnavailable =
    Boolean(selectedBindingId) &&
    !loadingApps &&
    !appsError &&
    !activeFeishuApps.some((app) => app.id === selectedBindingId);
  // 群聊与手机号都由所选应用寻址，应用不可用时一并禁用；Webhook 走独立 HTTP，保持可用。
  const appTargetDisabled = noUsableApp || boundAppUnavailable;
  // 失效应用绝不放进 options：SearchableSelect 的选项没有 disabled 概念，
  // 放进去就能被重新选中并提交一个后端必然 400 的值。
  const feishuAppOptions: SearchableSelectOption[] = [
    { value: '', label: '自动（使用最新启用的飞书应用）' },
    ...activeFeishuApps.map((app) => ({
      value: app.id,
      label: feishuAppLabel(app),
      keywords: [app.app_id || '', app.bot_name || ''].filter(Boolean),
    })),
  ];

  function loadFeishuApps() {
    setLoadingApps(true);
    setAppsError('');
    api
      .get<FeishuAppRead[]>(
        `/api/enterprise/scheduled-tasks/feishu-apps?tenant_id=${TENANT_ID}`,
      )
      .then((data) => setFeishuApps(Array.isArray(data) ? data : []))
      .catch((error) => {
        setFeishuApps([]);
        setAppsError(error instanceof Error ? error.message : '加载飞书应用列表失败');
      })
      .finally(() => setLoadingApps(false));
  }

  useEffect(() => {
    loadFeishuApps();
  }, []);

  // 应用决定群聊可见范围，切换后必须重拉。cancelled 守卫防"后发先至"把 A 应用的
  // 群列表盖到 B 应用上；手动刷新按钮复用同一路径（chatsReloadToken 递增）。
  useEffect(() => {
    if (!values.feishu_notify.enabled) return;
    let cancelled = false;
    setLoadingChats(true);
    setChatsError('');
    const scope = selectedBindingId
      ? `&binding_id=${encodeURIComponent(selectedBindingId)}`
      : '';
    api
      .get<FeishuChat[]>(
        `/api/enterprise/scheduled-tasks/feishu-chats?tenant_id=${TENANT_ID}${scope}`,
      )
      .then((data) => {
        if (cancelled) return;
        setFeishuChats(Array.isArray(data) ? data : []);
      })
      .catch((error) => {
        if (cancelled) return;
        // 空态与失败态必须分开：把"凭证失效"渲染成"该应用没加入任何群聊"会误导排障。
        setFeishuChats([]);
        setChatsError(
          error instanceof Error ? error.message : '拉取群聊失败，请检查应用凭证或网络后重试。',
        );
      })
      .finally(() => {
        if (!cancelled) setLoadingChats(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedBindingId, values.feishu_notify.enabled, chatsReloadToken]);

  useEffect(() => {
    let cancelled = false;
    api
      .get<Array<{ open_id: string; display_name: string; user_id: string }>>(
        `/api/enterprise/scheduled-tasks/feishu-recipients?tenant_id=${TENANT_ID}`,
      )
      .then((res) => {
        if (!cancelled) setFeishuRecipients(res || []);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleDuplicate() {
    if (!taskId) return;
    try {
      const duplicated = await api.post<ScheduledTaskRead>(
        `/api/enterprise/scheduled-tasks/${taskId}/duplicate?tenant_id=${TENANT_ID}&reset_recipients=true`,
      );
      notify.success(`已复制任务 "${duplicated.title}"，推送目标已独立重置`);
      navigate(`/enterprise/scheduled-tasks/${duplicated.id}/edit`);
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '复制任务失败');
    }
  }

  function handleClearNotifyTargets() {
    setValues((prev) => ({
      ...prev,
      feishu_notify: {
        ...prev.feishu_notify,
        chat_ids: [],
        chat_names: [],
        webhooks: [],
        mobiles: [],
        open_ids: [],
        chat_id: '',
        chat_name: '',
        webhook_url: '',
      },
    }));
    notify.info('已清空当前任务的所有推送目标，请按需单独配置');
  }

  async function handleTestNotify() {
    if (!values.feishu_notify.enabled) {
      notify.error('请先开启飞书出站推送开关');
      return;
    }
    const hasTargets =
      (values.feishu_notify.webhooks || []).length > 0 ||
      (values.feishu_notify.chat_ids || []).length > 0 ||
      (values.feishu_notify.mobiles || []).length > 0 ||
      (values.feishu_notify.open_ids || []).length > 0;
    if (!hasTargets) {
      notify.error('请至少配置一个推送目标（群机器人 Webhook、群聊或责任人）');
      return;
    }
    setTestingNotify(true);
    setTestResult(null);
    try {
      const res = await api.post<{
        ok: boolean;
        sent_count: number;
        failed_count: number;
        sent: Array<{ target_type: string; identifier: string }>;
        failed: Array<{ target_type: string; identifier: string; error: string }>;
        error?: string;
      }>('/api/enterprise/scheduled-tasks/test-notify', {
        tenant_id: TENANT_ID,
        task_id: taskId || null,
        title: values.title || '定时任务',
        feishu_notify: values.feishu_notify,
      });
      setTestResult(res);
      if (res.ok) {
        notify.success(`测试推送完成：成功送达 ${res.sent_count} 个通道`);
      } else {
        notify.error(`测试推送未完全成功：${res.error || '部分目标推送失败'}`);
      }
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '测试推送失败');
    } finally {
      setTestingNotify(false);
    }
  }

  function handleFeishuAppChange(bindingId: string) {
    const app = feishuApps.find((item) => item.id === bindingId) || null;
    const hadChats = (values.feishu_notify.chat_ids || []).length > 0;
    setValues((prev) => ({
      ...prev,
      feishu_notify: switchFeishuApp(prev.feishu_notify, app),
    }));
    if (hadChats) notify.success('已切换应用，原已选群聊已清空，请重新选择目标群聊。');
  }

  function update<K extends keyof TaskFormValues>(key: K, value: TaskFormValues[K]) {
    setValues((prev) => ({ ...prev, [key]: value }));
  }

  function updateNotify<K extends keyof TaskFormValues['feishu_notify']>(
    key: K,
    value: TaskFormValues['feishu_notify'][K],
  ) {
    setValues((prev) => ({
      ...prev,
      feishu_notify: {
        ...prev.feishu_notify,
        [key]: value,
      },
    }));
  }

  function toggleChat(chatId: string, chatName: string) {
    const prevIds = values.feishu_notify.chat_ids || [];
    const prevNames = values.feishu_notify.chat_names || [];
    const isSelected = prevIds.includes(chatId);
    let nextIds: string[];
    let nextNames: string[];
    if (isSelected) {
      nextIds = prevIds.filter((id) => id !== chatId);
      const index = prevIds.indexOf(chatId);
      nextNames = prevNames.filter((_, i) => i !== index);
    } else {
      nextIds = [...prevIds, chatId];
      nextNames = [...prevNames, chatName];
    }
    setValues((prev) => ({
      ...prev,
      feishu_notify: {
        ...prev.feishu_notify,
        chat_ids: nextIds,
        chat_names: nextNames,
        chat_id: nextIds[0] || '',
        chat_name: nextNames[0] || '',
      },
    }));
  }

  function addRecipient(raw: string) {
    const list = raw
      .split(/[,，\s]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    if (!list.length) return;
    const currentMobiles = values.feishu_notify.mobiles || [];
    const currentOpenIds = values.feishu_notify.open_ids || [];
    const mobileSet = new Set(currentMobiles);
    const openIdSet = new Set(currentOpenIds);
    const newMobiles: string[] = [];
    const newOpenIds: string[] = [];

    for (const item of list) {
      if (item.startsWith('ou_') || item.startsWith('on_')) {
        if (!openIdSet.has(item)) {
          openIdSet.add(item);
          newOpenIds.push(item);
        }
      } else {
        if (!mobileSet.has(item)) {
          mobileSet.add(item);
          newMobiles.push(item);
        }
      }
    }

    setValues((prev) => ({
      ...prev,
      feishu_notify: {
        ...prev.feishu_notify,
        mobiles: [...currentMobiles, ...newMobiles],
        open_ids: [...currentOpenIds, ...newOpenIds],
      },
    }));
    setMobileInput('');
  }

  function removeRecipient(id: string) {
    setValues((prev) => ({
      ...prev,
      feishu_notify: {
        ...prev.feishu_notify,
        mobiles: (prev.feishu_notify.mobiles || []).filter((m) => m !== id),
        open_ids: (prev.feishu_notify.open_ids || []).filter((o) => o !== id),
      },
    }));
  }

  function getRecipientLabel(openId: string): string {
    const found = feishuRecipients.find((r) => r.open_id === openId);
    return found ? `${found.display_name} (${openId.slice(0, 8)}...)` : openId;
  }

  function addWebhooks(raw: string) {
    const list = raw
      .split(/[\n,，\s]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    if (!list.length) return;
    const current = values.feishu_notify.webhooks || [];
    const set = new Set(current);
    const added: string[] = [];
    for (const u of list) {
      if (!u.startsWith('http://') && !u.startsWith('https://')) {
        notify.error('Webhook 地址必须以 http:// 或 https:// 开头');
        return;
      }
      if (!set.has(u)) {
        set.add(u);
        added.push(u);
      }
    }
    if (added.length) {
      updateNotify('webhooks', [...current, ...added]);
    }
    setWebhookInput('');
  }

  function removeWebhook(index: number) {
    updateNotify(
      'webhooks',
      (values.feishu_notify.webhooks || []).filter((_, i) => i !== index),
    );
  }

  useEffect(() => {
    const onScopeChange = (event: Event) => {
      const next = (event as CustomEvent<{ agentId?: string }>).detail?.agentId || '';
      setAgentId(next && !isTeamScope(next) ? next : readEmployeeScope());
    };
    window.addEventListener('ultrarag-enterprise-agent-scope-change', onScopeChange);
    return () => window.removeEventListener('ultrarag-enterprise-agent-scope-change', onScopeChange);
  }, []);

  useEffect(() => {
    if (!isEdit) {
      setValues(INITIAL_VALUES);
      return;
    }
    if (!taskId) return;
    setLoading(true);
    api
      .get<ScheduledTaskRead>(`/api/enterprise/scheduled-tasks/${taskId}?tenant_id=${TENANT_ID}`)
      .then((row) => {
        setAgentId(row.agent_id);
        setTaskMetadata(row.metadata || {});
        setValues(taskToFormValues(row));
      })
      .catch((error) => notify.error(error instanceof Error ? error.message : '加载定时任务失败'))
      .finally(() => setLoading(false));
  }, [isEdit, taskId]);

  useEffect(() => {
    if (!agentId) {
      setSops([]);
      return;
    }
    let cancelled = false;
    api
      .get<SkillRead[]>(
        `/api/enterprise/skills?tenant_id=${TENANT_ID}&agent_id=${encodeURIComponent(agentId)}`,
      )
      .then((rows) => {
        if (cancelled) return;
        setSops(scheduledTaskSopOptions(rows));
      })
      .catch(() => {
        if (!cancelled) setSops([]);
      });
    return () => {
      cancelled = true;
    };
  }, [agentId]);

  function validate(): boolean {
    const nextErrors: FormErrors = {};
    if (!values.title.trim()) nextErrors.title = '请填写任务名称';
    if (!values.prompt.trim()) nextErrors.prompt = '请填写任务描述';
    if (values.schedule_type === 'once') {
      if (!values.run_at) nextErrors.run_at = '请选择执行时间';
    } else if (values.schedule_type === 'interval') {
      if (!values.interval_minutes || values.interval_minutes < 1) {
        nextErrors.interval_minutes = '执行间隔至少为 1 分钟';
      }
    } else if (values.schedule_type === 'daily') {
      if (!values.times?.length && !values.time) {
        nextErrors.time = '请至少配置一个执行时段';
      }
    } else if (!values.time) {
      nextErrors.time = '请填写执行时间';
    }
    if (values.schedule_type === 'weekly' && !values.weekdays.length) {
      nextErrors.weekdays = '请选择星期';
    }
    if (values.feishu_notify.enabled && boundAppUnavailable) {
      notify.error('所选飞书应用不可用，请重新选择。');
      return false;
    }
    setErrors(nextErrors);
    return Object.keys(nextErrors).length === 0;
  }

  async function save() {
    if (!validate()) return;
    if (!agentId) {
      notify.error('请先选择员工');
      return;
    }
    const metadata: Record<string, unknown> = {
      ...taskMetadata,
      ...(values.sop_id
        ? {
            sop_id: values.sop_id,
            sop_version_policy: values.sop_version_policy,
          }
        : {}),
      feishu_notify: values.feishu_notify,
    };
    if (!values.sop_id) {
      delete metadata.sop_id;
      delete metadata.sop_version_policy;
      delete metadata.sop_version;
    }
    const payload = {
      tenant_id: TENANT_ID,
      agent_id: agentId,
      title: values.title.trim(),
      prompt: values.prompt.trim(),
      description: values.description?.trim() || undefined,
      schedule_type: values.schedule_type,
      schedule: buildSchedule(values),
      timezone: getClientTimeZone(),
      status: values.status,
      concurrency_policy: 'forbid',
      misfire_policy: 'coalesce',
      max_runs: values.max_runs || undefined,
      metadata,
    };
    setSaving(true);
    try {
      const saved =
        isEdit && taskId
          ? await api.put<ScheduledTaskRead>(`/api/enterprise/scheduled-tasks/${taskId}`, payload)
          : await api.post<ScheduledTaskRead>('/api/enterprise/scheduled-tasks', payload);
      notify.success('定时任务已保存');
      if (!isEdit) {
        navigate(`/enterprise/scheduled-tasks/${saved.id}/edit`, { replace: true });
      } else {
        setTaskMetadata(saved.metadata || {});
        setValues(taskToFormValues(saved));
      }
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '保存定时任务失败');
    } finally {
      setSaving(false);
    }
  }

  function toggleWeekday(day: number, checked: boolean) {
    setValues((prev) => {
      const next = checked
        ? [...prev.weekdays, day]
        : prev.weekdays.filter((item) => item !== day);
      return { ...prev, weekdays: next.sort((a, b) => a - b) };
    });
  }

  return (
    <div
      className="min-h-full box-border px-[48px] pt-[32px] pb-[43px] max-[900px]:px-[16px]"
      aria-busy={loading || saving}
    >
      <AppHeader
        onLogout={onLogout}
        userName={currentUser?.username}
        title={isEdit ? '编辑定时任务' : '新建空白定时任务'}
        description="保存后到点会拉起一个新的执行记录，并交给当前员工按 SOP、技能、资料和工具执行。"
      />
      {/* Sticky Action Bar */}
      <div className="sticky top-[-22px] z-30 -mx-[48px] max-[900px]:-mx-[16px] px-[48px] max-[900px]:px-[16px] py-[12px] my-[16px] bg-[var(--background)]/90 backdrop-blur-md border-b border-[#eceef1] flex flex-wrap items-center justify-between gap-[12px] shadow-2xs">
        <div className="flex items-center gap-[8px] min-w-0">
          <span className="text-[14px] font-medium text-[#18181a] truncate">
            {values.title.trim() || (isEdit ? '编辑定时任务' : '新建空白定时任务')}
          </span>
          <span className="inline-flex items-center rounded-md bg-[#f1f3f7] px-[6px] py-[1px] text-[11px] font-medium text-[#5a6275]">
            {values.status === 'active' ? '启用' : '已暂停'}
          </span>
        </div>

        <div className="flex items-center gap-[12px] shrink-0">
          <Button
            variant="outline"
            onClick={() => navigate('/enterprise/scheduled-tasks')}
            className="h-8 gap-1 rounded-[10px] border-[0.5px] border-[#e3e7f1] bg-white px-4 text-[12px] font-normal text-[#757f9c] hover:border-[#cbd3e6] hover:bg-white hover:text-[#18181a]"
          >
            <IconArrowRight className="size-3.5 rotate-180" />
            返回定时任务
          </Button>
          {isEdit && (
            <Button
              type="button"
              variant="outline"
              onClick={() => void handleDuplicate()}
              disabled={saving}
              className="h-8 gap-1.5 rounded-[10px] border-[0.5px] border-[#3370ff] bg-white px-4 text-[12px] font-medium text-[#3370ff] hover:bg-[#f0f6ff]"
            >
              <Copy className="size-3.5" />
              复制任务
            </Button>
          )}
          <Button
            onClick={() => void save()}
            disabled={saving}
            className="h-8 gap-1 rounded-[10px] bg-[#18181a] px-5 text-[12px] font-normal text-white hover:bg-[#303030]"
          >
            保存
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-1 items-start gap-[20px] lg:grid-cols-2">
        <section className={CARD_CLASS}>
          <h3 className={CARD_TITLE_CLASS}>任务说明</h3>
          <div className="flex flex-col gap-[16px]">
            <div className="flex flex-col gap-[6px]">
              <Label htmlFor="task-title" className={FIELD_LABEL_CLASS}>
                任务名称
              </Label>
              <div className="relative">
                <IconAlarm className="pointer-events-none absolute left-[10px] top-1/2 size-[14px] -translate-y-1/2 text-[#858b9c]" />
                <Input
                  id="task-title"
                  className={cn('pl-[30px]', errors.title && 'border-destructive')}
                  maxLength={80}
                  placeholder="例如：每日交付质量复盘"
                  value={values.title}
                  onChange={(event) => update('title', event.target.value)}
                />
              </div>
              {errors.title && <p className={FIELD_ERROR_CLASS}>{errors.title}</p>}
            </div>

            <div className="flex flex-col gap-[6px]">
              <Label htmlFor="task-prompt" className={FIELD_LABEL_CLASS}>
                每次执行时交给员工的任务
              </Label>
              <Textarea
                id="task-prompt"
                rows={7}
                maxLength={10000}
                className={cn(errors.prompt && 'border-destructive')}
                placeholder="描述员工每次执行时需要做什么，可以包含拆解要求、输出格式和注意事项。"
                value={values.prompt}
                onChange={(event) => update('prompt', event.target.value)}
              />
              <div className="flex items-center justify-between">
                {errors.prompt ? (
                  <p className={FIELD_ERROR_CLASS}>{errors.prompt}</p>
                ) : (
                  <span />
                )}
                <span className="text-[12px] leading-none text-[#858b9c]">
                  {values.prompt.length}/10000
                </span>
              </div>
            </div>

            <div className="flex flex-col gap-[6px]">
              <Label className={FIELD_LABEL_CLASS}>指定 SOP</Label>
              <Select
                value={values.sop_id || '__auto__'}
                onValueChange={(value) => {
                  const sopId = value === '__auto__' ? '' : value;
                  setValues((prev) => ({
                    ...prev,
                    sop_id: sopId,
                    sop_version_policy: sopId ? prev.sop_version_policy : 'latest',
                  }));
                }}
              >
                <SelectTrigger className="w-full">
                  <SelectValue placeholder="由 Harness v2 自动判断" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__auto__">由 Harness v2 自动判断</SelectItem>
                  {sops.map((sop) => (
                    <SelectItem key={sop.skill_id} value={sop.skill_id}>
                      {sop.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-[12px] leading-[18px] text-[#858b9c]">
                选择后每次唤醒都会由 Harness v2 强制启动该 SOP；未选择时仍由模型按任务判断。
              </p>
            </div>

            {values.sop_id && (
              <div className="flex flex-col gap-[6px]">
                <Label className={FIELD_LABEL_CLASS}>SOP 版本策略</Label>
                <Select
                  value={values.sop_version_policy}
                  onValueChange={(value) =>
                    update('sop_version_policy', value === 'pinned' ? 'pinned' : 'latest')
                  }
                >
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="latest">始终使用最新发布版本</SelectItem>
                    <SelectItem value="pinned">固定使用保存时版本</SelectItem>
                  </SelectContent>
                </Select>
                <p className="text-[12px] leading-[18px] text-[#858b9c]">
                  {values.sop_version_policy === 'latest'
                    ? '每次运行前重新读取当前员工可用的最新已发布 SOP。'
                    : `保存时锁定主 SOP 及其嵌套子 SOP${
                        typeof taskMetadata.sop_version === 'string' && taskMetadata.sop_version
                          ? `（当前固定版本 ${taskMetadata.sop_version}）`
                          : ''
                      }；后续发布新版本不会影响该任务。`}
                </p>
              </div>
            )}

            <div className="flex flex-col gap-[6px]">
              <Label htmlFor="task-description" className={FIELD_LABEL_CLASS}>
                内部备注
              </Label>
              <Textarea
                id="task-description"
                rows={3}
                placeholder="可选，用于说明这个定时任务的来源和目的"
                value={values.description || ''}
                onChange={(event) => update('description', event.target.value)}
              />
            </div>
          </div>
        </section>

        <section className={CARD_CLASS}>
          <h3 className={CARD_TITLE_CLASS}>唤醒计划</h3>
          <div className="flex flex-col gap-[16px]">
            <div className="flex items-center justify-between">
              <Label htmlFor="task-status" className={FIELD_LABEL_CLASS}>
                启用状态
              </Label>
              <div className="flex items-center gap-[8px]">
                <Switch
                  id="task-status"
                  checked={values.status !== 'paused'}
                  onCheckedChange={(checked) => update('status', checked ? 'active' : 'paused')}
                />
                <span className="text-[13px] text-[#858b9c]">
                  {values.status !== 'paused' ? '启用' : '暂停'}
                </span>
              </div>
            </div>

            <div className="flex flex-col gap-[6px]">
              <Label className={FIELD_LABEL_CLASS}>调度类型</Label>
              <Select
                value={values.schedule_type}
                onValueChange={(value) => {
                  const nextType = value as TaskFormValues['schedule_type'];
                  setValues((prev) => {
                    const next = { ...prev, schedule_type: nextType };
                    if (nextType === 'daily' && (!next.times || next.times.length === 0)) {
                      next.times = [next.time || '09:00'];
                    }
                    return next;
                  });
                }}
              >
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="interval">间隔循环（按分钟）</SelectItem>
                  <SelectItem value="daily">每天</SelectItem>
                  <SelectItem value="weekly">每周</SelectItem>
                  <SelectItem value="monthly">每月</SelectItem>
                  <SelectItem value="once">一次性</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {scheduleType === 'interval' ? (
              <div className="flex flex-col gap-[6px]">
                <Label htmlFor="task-interval-minutes" className={FIELD_LABEL_CLASS}>
                  执行间隔（分钟）
                </Label>
                <div className="flex items-center gap-[8px]">
                  <Input
                    id="task-interval-minutes"
                    type="number"
                    min={1}
                    max={1440}
                    className={cn('w-[140px]', errors.interval_minutes && 'border-destructive')}
                    value={values.interval_minutes ?? 1}
                    onChange={(event) =>
                      update('interval_minutes', Math.max(1, Number(event.target.value) || 1))
                    }
                  />
                  <span className="text-[13px] text-[#858b9c]">分钟执行一次</span>
                </div>
                {errors.interval_minutes && (
                  <p className={FIELD_ERROR_CLASS}>{errors.interval_minutes}</p>
                )}
                <p className="text-[12px] leading-[18px] text-[#858b9c]">
                  设置后，数字员工将在启用后每隔指定分钟数自动唤醒执行一次任务。
                </p>
              </div>
            ) : scheduleType === 'once' ? (
              <div className="flex flex-col gap-[6px]">
                <Label htmlFor="task-run-at" className={FIELD_LABEL_CLASS}>
                  执行时间
                </Label>
                <Input
                  id="task-run-at"
                  type="datetime-local"
                  className={cn(errors.run_at && 'border-destructive')}
                  value={values.run_at}
                  onChange={(event) => update('run_at', event.target.value)}
                />
                {errors.run_at && <p className={FIELD_ERROR_CLASS}>{errors.run_at}</p>}
              </div>
            ) : scheduleType === 'daily' ? (
              <div className="flex flex-col gap-[10px]">
                <div className="flex items-center justify-between">
                  <Label className={FIELD_LABEL_CLASS}>执行时段（支持每日多时段推送）</Label>
                  <span className="text-[12px] text-[#858b9c]">
                    每日共 {dailyTimes.length} 次
                  </span>
                </div>

                {/* Configured Time Badges */}
                <div className="flex flex-wrap items-center gap-[8px]">
                  {dailyTimes.map((t) => (
                    <span
                      key={t}
                      className="inline-flex items-center gap-[6px] rounded-[8px] border border-[#d8dce6] bg-[#f8f9fb] px-[10px] py-[5px] text-[13px] font-medium text-[#18181a] shadow-2xs"
                    >
                      <span>{t}</span>
                      {dailyTimes.length > 1 && (
                        <button
                          type="button"
                          onClick={() => removeDailyTime(t)}
                          className="text-[#9ea3b5] hover:text-[#d20b0b] transition-colors"
                          title="移除该时段"
                        >
                          <X className="size-[13px]" />
                        </button>
                      )}
                    </span>
                  ))}
                </div>

                {/* Add New Time */}
                <div className="flex items-center gap-[8px]">
                  <Input
                    type="time"
                    className="w-[140px]"
                    value={newTimeInput}
                    onChange={(e) => setNewTimeInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault();
                        addDailyTime();
                      }
                    }}
                  />
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => addDailyTime()}
                    className="gap-[4px]"
                  >
                    <Plus className="size-[13px]" />
                    <span>添加时段</span>
                  </Button>
                </div>

                {/* Quick Presets */}
                <div className="flex flex-wrap items-center gap-[6px] pt-[2px]">
                  <span className="text-[12px] text-[#858b9c]">快捷模板:</span>
                  <button
                    type="button"
                    onClick={() => applyTimePreset(['09:00', '13:00', '20:00'])}
                    className="rounded-[6px] bg-[#f1f3f7] px-[8px] py-[3px] text-[12px] text-[#4f566b] transition-colors hover:bg-[#e4e7ed] hover:text-[#18181a]"
                  >
                    早中晚三频 (09:00, 13:00, 20:00)
                  </button>
                  <button
                    type="button"
                    onClick={() => applyTimePreset(['09:30', '18:30'])}
                    className="rounded-[6px] bg-[#f1f3f7] px-[8px] py-[3px] text-[12px] text-[#4f566b] transition-colors hover:bg-[#e4e7ed] hover:text-[#18181a]"
                  >
                    上下班双报 (09:30, 18:30)
                  </button>
                  <button
                    type="button"
                    onClick={() => applyTimePreset(['09:00', '12:00', '15:00', '18:00'])}
                    className="rounded-[6px] bg-[#f1f3f7] px-[8px] py-[3px] text-[12px] text-[#4f566b] transition-colors hover:bg-[#e4e7ed] hover:text-[#18181a]"
                  >
                    工作四频 (09:00, 12:00, 15:00, 18:00)
                  </button>
                </div>

                <p className="text-[12px] leading-[18px] text-[#858b9c]">
                  设置后，数字员工每天将在 {dailyTimes.join('、')} 自动唤醒并执行，每次执行生成独立运行记录并向飞书推送。
                </p>
                {errors.time && <p className={FIELD_ERROR_CLASS}>{errors.time}</p>}
              </div>
            ) : (
              <div className="flex flex-col gap-[6px]">
                <Label htmlFor="task-time" className={FIELD_LABEL_CLASS}>
                  执行时间
                </Label>
                <Input
                  id="task-time"
                  type="time"
                  className={cn(errors.time && 'border-destructive')}
                  value={values.time}
                  onChange={(event) => update('time', event.target.value)}
                />
                {errors.time && <p className={FIELD_ERROR_CLASS}>{errors.time}</p>}
              </div>
            )}

            {scheduleType === 'weekly' && (
              <div className="flex flex-col gap-[8px]">
                <Label className={FIELD_LABEL_CLASS}>执行日期</Label>
                <div className="flex flex-wrap gap-x-[16px] gap-y-[10px]">
                  {WEEKDAY_OPTIONS.map((option) => (
                    <label
                      key={option.value}
                      className="flex cursor-pointer items-center gap-[6px] text-[13px] text-[#18181a]"
                    >
                      <Checkbox
                        checked={values.weekdays.includes(option.value)}
                        onCheckedChange={(checked) =>
                          toggleWeekday(option.value, checked === true)
                        }
                      />
                      {option.label}
                    </label>
                  ))}
                </div>
                {errors.weekdays && <p className={FIELD_ERROR_CLASS}>{errors.weekdays}</p>}
              </div>
            )}

            {scheduleType === 'monthly' && (
              <div className="flex flex-col gap-[6px]">
                <Label htmlFor="task-day" className={FIELD_LABEL_CLASS}>
                  每月几号
                </Label>
                <Input
                  id="task-day"
                  type="number"
                  min={1}
                  max={31}
                  className="w-[120px]"
                  value={values.day_of_month}
                  onChange={(event) => update('day_of_month', Number(event.target.value) || 1)}
                />
              </div>
            )}

            <div className="flex flex-col gap-[6px]">
              <Label htmlFor="task-max-runs" className={FIELD_LABEL_CLASS}>
                最大运行次数
              </Label>
              <Input
                id="task-max-runs"
                type="number"
                min={1}
                placeholder="不填为无限制"
                value={values.max_runs ?? ''}
                onChange={(event) =>
                  update('max_runs', event.target.value ? Number(event.target.value) : undefined)
                }
              />
            </div>

            <div className="rounded-[12px] border border-[#eef0f4] bg-[#fafbfc] px-[14px] py-[12px] text-[13px] leading-[1.6] text-[#858b9c]">
              默认使用 forbid 并发策略：上一轮未结束时跳过本次唤醒，避免同一员工重复处理同一批任务。
            </div>
          </div>
        </section>

        {/* 飞书企业消息推送配置卡片 */}
        <section className={cn(CARD_CLASS, 'lg:col-span-2')}>
          <div className="flex items-center justify-between pb-[16px] border-b border-[#f0f2f5]">
            <div className="flex items-center gap-[10px]">
              <div className="flex size-[32px] items-center justify-center rounded-[8px] bg-[#3370ff]/10 text-[#3370ff]">
                <Bell className="size-4" />
              </div>
              <div>
                <h3 className="text-[14px] font-semibold text-[#18181a] flex items-center gap-2">
                  飞书消息通知配置
                  <span className="rounded-full bg-[#e8f0ff] px-2 py-0.5 text-[11px] font-medium text-[#3370ff]">
                    独立配置 · 任务隔离
                  </span>
                </h3>
                <p className="text-[12px] text-[#858b9c]">
                  每个定时任务独立配置推送目标（群聊、责任人、Webhook），任务间互不影响。
                </p>
              </div>
            </div>
            <div className="flex items-center gap-[12px]">
              {values.feishu_notify.enabled && (
                <>
                  {((values.feishu_notify.chat_ids || []).length > 0 ||
                    (values.feishu_notify.webhooks || []).length > 0 ||
                    (values.feishu_notify.mobiles || []).length > 0 ||
                    (values.feishu_notify.open_ids || []).length > 0) && (
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={handleClearNotifyTargets}
                      className="text-[12px] text-[#858b9c] hover:text-[#d20b0b] cursor-pointer"
                    >
                      清空推送目标
                    </Button>
                  )}
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={testingNotify}
                    onClick={handleTestNotify}
                    className="flex items-center gap-1.5 text-[12px] text-[#3370ff] border-[#3370ff]/30 hover:bg-[#3370ff]/5 cursor-pointer"
                  >
                    <Send className={cn('size-3.5', testingNotify && 'animate-spin')} />
                    {testingNotify ? '正在测试推送...' : '发送测试推送'}
                  </Button>
                </>
              )}
              <div className="flex items-center gap-[8px]">
                <Switch
                  id="feishu-notify-enabled"
                  checked={values.feishu_notify.enabled}
                  onCheckedChange={(checked) => updateNotify('enabled', checked)}
                />
                <Label htmlFor="feishu-notify-enabled" className="text-[13px] text-[#858b9c] cursor-pointer">
                  {values.feishu_notify.enabled ? '已启用推送' : '未启用'}
                </Label>
              </div>
            </div>
          </div>

          {testResult && (
            <div
              className={cn(
                'mt-[16px] p-[14px] rounded-[10px] border text-[13px] flex flex-col gap-2',
                testResult.ok
                  ? 'bg-[#f6ffed] border-[#b7eb8f] text-[#389e0d]'
                  : 'bg-[#fff1f0] border-[#ffa39e] text-[#cf1322]',
              )}
            >
              <div className="flex items-center justify-between font-semibold">
                <div className="flex items-center gap-2">
                  {testResult.ok ? (
                    <CheckCircle2 className="size-4 text-[#52c41a]" />
                  ) : (
                    <AlertCircle className="size-4 text-[#ff4d4f]" />
                  )}
                  <span>
                    {testResult.ok
                      ? `测试推送成功！共成功送达 ${testResult.sent_count} 个通道`
                      : `测试推送未完全成功：成功 ${testResult.sent_count} 个，失败 ${testResult.failed_count} 个`}
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => setTestResult(null)}
                  className="text-xs text-[#858b9c] hover:text-[#18181a] cursor-pointer"
                >
                  关闭
                </button>
              </div>

              {testResult.sent.length > 0 && (
                <div className="text-[12px] text-[#389e0d] pl-6 flex flex-wrap gap-2">
                  <span>成功目标：</span>
                  {testResult.sent.map((s, i) => (
                    <span key={i} className="inline-block font-mono bg-white/70 px-1.5 py-0.5 rounded border border-[#b7eb8f]">
                      [{s.target_type}] {s.identifier}
                    </span>
                  ))}
                </div>
              )}

              {testResult.failed.length > 0 && (
                <div className="text-[12px] text-[#cf1322] pl-6 flex flex-col gap-1.5">
                  <span>失败目标及诊断原因：</span>
                  {testResult.failed.map((f, i) => (
                    <div key={i} className="font-mono bg-white/90 p-2 rounded border border-[#ffa39e] text-[12px] leading-relaxed">
                      <strong>[{f.target_type}] {f.identifier}:</strong> {f.error}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {values.feishu_notify.enabled && (
            <div className="mt-[20px] flex flex-col gap-[20px]">
              {/* 子模块 1: 飞书自建应用通道 (多群聊 + 责任人私聊) */}
              <div className="rounded-[12px] border border-[#eef0f4] bg-[#fafbfc] p-[16px] flex flex-col gap-[16px]">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <div className="size-2 rounded-full bg-[#3370ff]" />
                    <h4 className="text-[13px] font-semibold text-[#18181a]">
                      飞书企业自建应用通道
                    </h4>
                    <span className="rounded-full bg-[#e8f0ff] px-2 py-0.5 text-[10px] font-medium text-[#3370ff]">
                      官方推荐 · 权限受控
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() => setChatsReloadToken((token) => token + 1)}
                    disabled={loadingChats}
                    className="flex items-center gap-[4px] text-[12px] text-[#3370ff] hover:text-[#2558d4] disabled:opacity-50 cursor-pointer"
                  >
                    <RotateCw className={cn('size-3.5', loadingChats && 'animate-spin')} />
                    {loadingChats ? '正在拉取群聊...' : '刷新应用群列表'}
                  </button>
                </div>

                {/* 1.0 飞书应用选择：决定群聊列表与手机号反查的范围 */}
                <div className="flex flex-col gap-[8px]">
                  <div className="flex items-center gap-[8px]">
                    <Label className={FIELD_LABEL_CLASS}>飞书应用</Label>
                    <SearchableSelect
                      value={selectedBindingId}
                      onValueChange={handleFeishuAppChange}
                      options={feishuAppOptions}
                      placeholder={
                        boundAppUnavailable ? '已停用（请重新选择）' : '请选择飞书应用'
                      }
                      searchPlaceholder="搜索飞书应用"
                      emptyText="无匹配的飞书应用"
                      disabled={loadingApps || noUsableApp}
                      className="w-[280px]"
                    />
                  </div>
                  <p className="text-[12px] leading-[18px] text-[#858b9c]">
                    选择推送所使用的飞书应用，群聊列表与手机号反查均基于该应用。
                  </p>
                  {noUsableApp && (
                    <div className="flex items-center gap-[8px] rounded-[8px] border border-[#ffe0b2] bg-[#fff8e6] px-[12px] py-[8px] text-[12px] text-[#a35a00]">
                      <AlertTriangle className="size-[14px] shrink-0" />
                      <span className="flex-1">
                        当前企业尚未接入可用的飞书应用，请先在渠道管理中完成飞书自建应用接入。
                      </span>
                      <button
                        type="button"
                        onClick={() => navigate(EnterpriseRoute.Channels)}
                        className="shrink-0 font-medium text-[#3370ff] hover:text-[#2558d4] cursor-pointer"
                      >
                        前往渠道管理
                      </button>
                    </div>
                  )}
                  {boundAppUnavailable && (
                    <div className="flex items-center gap-[8px] rounded-[8px] border border-[#ffe0b2] bg-[#fff8e6] px-[12px] py-[8px] text-[12px] text-[#a35a00]">
                      <AlertTriangle className="size-[14px] shrink-0" />
                      <span>原绑定的飞书应用已停用或删除，请重新选择。</span>
                    </div>
                  )}
                  {appsError && (
                    <p className="text-[12px] leading-[18px] text-[#d20b0b]">
                      {appsError}
                    </p>
                  )}
                </div>

                {/* 1.1 目标群聊多选 */}
                <div className="flex flex-col gap-[8px]">
                  <div className="flex items-center justify-between">
                    <Label className={FIELD_LABEL_CLASS}>
                      目标群聊（可多选多个群）
                    </Label>
                    <span className="text-[12px] text-[#858b9c]">
                      已选{' '}
                      <strong className="text-[#3370ff]">
                        {(values.feishu_notify.chat_ids || []).length}
                      </strong>{' '}
                      个群聊
                    </span>
                  </div>

                  {feishuChats.length === 0 ? (
                    <div
                      className={cn(
                        'rounded-[8px] border border-dashed bg-white p-[16px] text-center text-[12px]',
                        chatsError
                          ? 'border-[#f38989] text-[#d20b0b]'
                          : 'border-[#ccd3e0] text-[#858b9c]',
                      )}
                    >
                      {chatsError
                        ? chatsError
                        : loadingChats
                          ? '正在连接飞书应用拉取群聊列表中...'
                          : boundAppUnavailable
                            ? '所选飞书应用已停用，无法拉取群聊或反查手机号。'
                            : '未检测到应用机器人已加入的飞书群聊。请在飞书群中将本应用机器人拉入群聊，然后点击右上角「刷新应用群列表」即可多选勾选。'}
                    </div>
                  ) : (
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-[8px] max-h-[220px] overflow-y-auto p-1 border border-[#eef0f4] rounded-[8px] bg-white">
                      {feishuChats.map((chat) => {
                        const isSelected = (values.feishu_notify.chat_ids || []).includes(
                          chat.chat_id,
                        );
                        return (
                          <div
                            key={chat.chat_id}
                            onClick={() => {
                              if (appTargetDisabled) return;
                              toggleChat(chat.chat_id, chat.name);
                            }}
                            className={cn(
                              'flex items-center gap-[10px] p-[10px] rounded-[8px] border transition-all',
                              appTargetDisabled &&
                                'cursor-not-allowed border-[#eef0f4] bg-white opacity-60',
                              !appTargetDisabled &&                                (isSelected
                                  ? 'cursor-pointer border-[#3370ff] bg-[#3370ff]/5 ring-1 ring-[#3370ff]/30'
                                  : 'cursor-pointer border-[#eef0f4] bg-white hover:border-[#ccd3e0] hover:bg-[#fafbfc]'),
                            )}
                          >
                            <Checkbox
                              checked={isSelected}
                              onCheckedChange={() => toggleChat(chat.chat_id, chat.name)}
                              className="pointer-events-none"
                            />
                            <div className="flex size-[28px] shrink-0 items-center justify-center rounded-full bg-[#f0f2f5] text-[#858b9c]">
                              <Users className="size-3.5" />
                            </div>
                            <div className="flex flex-col min-w-0 flex-1">
                              <span className="text-[13px] font-medium text-[#18181a] truncate">
                                {chat.name}
                              </span>
                              <span className="text-[11px] text-[#858b9c] font-mono truncate">
                                {chat.chat_id}
                              </span>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                  <p className="text-[12px] leading-[18px] text-[#858b9c]">
                    勾选的群聊将在任务执行完毕后并行接收卡片，支持按需多选多个业务群或监控群。
                  </p>
                </div>

                {/* 1.2 责任人 (私聊直达 · 支持 OpenID / 手机号) */}
                <div className="flex flex-col gap-[8px] pt-[8px] border-t border-[#eef0f4]">
                  <div className="flex items-center justify-between">
                    <Label className={FIELD_LABEL_CLASS}>
                      接收责任人 (私聊直达 · 支持飞书 OpenID / 手机号)
                    </Label>
                    <span className="text-[12px] text-[#858b9c]">
                      已添加{' '}
                      <strong className="text-[#3370ff]">
                        {((values.feishu_notify.open_ids || []).length) + ((values.feishu_notify.mobiles || []).length)}
                      </strong>{' '}
                      位责任人
                    </span>
                  </div>

                  {/* 快捷添加已绑定责任人 */}
                  {feishuRecipients.length > 0 && (
                    <div className="flex flex-wrap items-center gap-[6px] p-[8px] rounded-[8px] bg-[#f0f4ff]/50 border border-[#e1e9ff]">
                      <span className="text-[11px] font-medium text-[#3370ff] flex items-center gap-1">
                        <Users className="size-3" />
                        快捷添加系统已绑定用户：
                      </span>
                      {feishuRecipients.map((rec) => {
                        const isAdded = (values.feishu_notify.open_ids || []).includes(rec.open_id);
                        return (
                          <button
                            key={rec.open_id}
                            type="button"
                            disabled={isAdded || appTargetDisabled}
                            onClick={() => addRecipient(rec.open_id)}
                            className={cn(
                              'inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full border transition cursor-pointer',
                              isAdded
                                ? 'bg-[#e2e8f0] text-[#94a3b8] border-[#cbd5e1] cursor-not-allowed'
                                : 'bg-white text-[#3370ff] border-[#b4cdfe] hover:bg-[#e8f0ff]',
                            )}
                          >
                            <UserCheck className="size-2.5" />
                            {rec.display_name}
                            {isAdded ? ' (已添加)' : ' +'}
                          </button>
                        );
                      })}
                    </div>
                  )}

                  {/* 已添加责任人标签列表 */}
                  {((values.feishu_notify.open_ids || []).length > 0 || (values.feishu_notify.mobiles || []).length > 0) && (
                    <div className="flex flex-wrap gap-[6px] p-[8px] rounded-[8px] bg-white border border-[#eef0f4]">
                      {(values.feishu_notify.open_ids || []).map((openId) => (
                        <span
                          key={openId}
                          className="inline-flex items-center gap-[6px] rounded-full bg-[#e8f0ff] px-[10px] py-[3px] text-[12px] font-medium text-[#3370ff]"
                        >
                          <UserCheck className="size-3 text-[#3370ff]" />
                          {getRecipientLabel(openId)}
                          <span className="text-[10px] bg-[#d0e2ff] px-1.5 py-0.2 rounded text-[#1a56db]">
                            直接私聊
                          </span>
                          <button
                            type="button"
                            onClick={() => removeRecipient(openId)}
                            className="hover:text-[#d20b0b] cursor-pointer"
                          >
                            <X className="size-3" />
                          </button>
                        </span>
                      ))}
                      {(values.feishu_notify.mobiles || []).map((mobile) => (
                        <span
                          key={mobile}
                          className="inline-flex items-center gap-[6px] rounded-full bg-[#fff7e6] border border-[#ffd591] px-[10px] py-[3px] text-[12px] font-medium text-[#d46b08]"
                        >
                          <Smartphone className="size-3 text-[#d46b08]" />
                          {mobile}
                          <span className="text-[10px] bg-[#ffe7ba] px-1.5 py-0.2 rounded text-[#d46b08]">
                            需通讯录权限
                          </span>
                          <button
                            type="button"
                            onClick={() => removeRecipient(mobile)}
                            className="hover:text-[#d20b0b] cursor-pointer"
                          >
                            <X className="size-3" />
                          </button>
                        </span>
                      ))}
                    </div>
                  )}

                  {/* 输入框与添加按钮 */}
                  <div className="flex items-center gap-[8px]">
                    <div className="relative flex-1">
                      <Smartphone className="pointer-events-none absolute left-[10px] top-1/2 size-[14px] -translate-y-1/2 text-[#858b9c]" />
                      <Input
                        className="pl-[30px]"
                        placeholder="输入责任人 OpenID (ou_...) 或手机号，支持逗号或空格分隔，按回车添加"
                        value={mobileInput}
                        disabled={appTargetDisabled}
                        onChange={(e) => setMobileInput(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') {
                            e.preventDefault();
                            addRecipient(mobileInput);
                          }
                        }}
                      />
                    </div>
                    <Button
                      type="button"
                      variant="outline"
                      className="shrink-0 text-[13px]"
                      disabled={appTargetDisabled}
                      onClick={() => addRecipient(mobileInput)}
                    >
                      <Plus className="size-3.5 mr-1" />
                      添加责任人
                    </Button>
                  </div>

                  <div className="rounded-[8px] bg-[#fffbe6] border border-[#ffe58f] p-[10px] text-[12px] text-[#ad6800] flex flex-col gap-1.5">
                    <p className="font-medium flex items-center gap-1">
                      <AlertCircle className="size-3.5 shrink-0" />
                      手机号推送提示（飞书权限说明）：
                    </p>
                    <p className="leading-[18px]">
                      通过手机号私聊需飞书自建应用开通敏感权限 <code>contact:user.phone:readonly</code>。若未开通，飞书官方会拒绝返回用户标识。
                    </p>
                    <p className="leading-[18px] text-[#593800]">
                      <strong>💡 免权限 20 秒直达方案：</strong>让该责任人在飞书中搜索机器人（或应用名称）发送任意私聊（如发“你好”），系统即可自动识别并在上方的「快捷添加系统已绑定用户」中出现，点击 <strong>+</strong> 即可 100% 稳定私聊推送！
                    </p>
                  </div>
                </div>
              </div>

              {/* 子模块 2: 自定义群机器人 Webhook 通道 (多选/支持多个) */}
              <div className="rounded-[12px] border border-[#eef0f4] bg-[#fafbfc] p-[16px] flex flex-col gap-[14px]">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <div className="size-2 rounded-full bg-[#ff7f00]" />
                    <h4 className="text-[13px] font-semibold text-[#18181a]">
                      自定义群机器人 Webhook 通道
                    </h4>
                    <span className="rounded-full bg-[#fff2e5] px-2 py-0.5 text-[10px] font-medium text-[#ff7f00]">
                      支持配置多个 Webhook
                    </span>
                  </div>
                  <span className="text-[12px] text-[#858b9c]">
                    已配置{' '}
                    <strong className="text-[#ff7f00]">
                      {(values.feishu_notify.webhooks || []).length}
                    </strong>{' '}
                    个机器人
                  </span>
                </div>

                {/* 已添加 Webhook 列表 */}
                {(values.feishu_notify.webhooks || []).length > 0 && (
                  <div className="flex flex-col gap-[6px]">
                    {values.feishu_notify.webhooks.map((wh, idx) => (
                      <div
                        key={idx}
                        className="flex items-center justify-between gap-[10px] p-[8px] px-[12px] rounded-[8px] bg-white border border-[#eef0f4]"
                      >
                        <div className="flex items-center gap-[8px] min-w-0 flex-1">
                          <Link2 className="size-3.5 text-[#ff7f00] shrink-0" />
                          <span className="text-[11px] font-mono text-[#858b9c] shrink-0">
                            Webhook #{idx + 1}
                          </span>
                          <span
                            className="text-[12px] font-mono text-[#18181a] truncate"
                            title={wh}
                          >
                            {wh}
                          </span>
                        </div>
                        <button
                          type="button"
                          onClick={() => removeWebhook(idx)}
                          className="text-[#858b9c] hover:text-[#d20b0b] cursor-pointer p-1"
                        >
                          <Trash2 className="size-3.5" />
                        </button>
                      </div>
                    ))}
                  </div>
                )}

                {/* Webhook 输入行 */}
                <div className="flex items-center gap-[8px]">
                  <div className="relative flex-1">
                    <Link2 className="pointer-events-none absolute left-[10px] top-1/2 size-[14px] -translate-y-1/2 text-[#858b9c]" />
                    <Input
                      className="pl-[30px]"
                      placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..."
                      value={webhookInput}
                      onChange={(e) => setWebhookInput(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') {
                          e.preventDefault();
                          addWebhooks(webhookInput);
                        }
                      }}
                    />
                  </div>
                  <Button
                    type="button"
                    variant="outline"
                    className="shrink-0 text-[13px]"
                    onClick={() => addWebhooks(webhookInput)}
                  >
                    <Plus className="size-3.5 mr-1" />
                    添加 Webhook
                  </Button>
                </div>
                <p className="text-[12px] leading-[18px] text-[#858b9c]">
                  适用于向未接入应用机器人的群或外部供应商飞书群群机器人派发卡片，支持添加多个 Webhook，调度中心将并行全部投递。
                </p>
              </div>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
