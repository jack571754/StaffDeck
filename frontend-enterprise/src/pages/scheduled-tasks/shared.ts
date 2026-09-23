import type { UnderlineTabItem } from '@/components/ui';
import { formatClientDateTime, parseBackendDateTime } from '@/lib/timezone';
import type { ScheduledTaskRead, ScheduledTaskRunRead } from '../../types';

export const ENTERPRISE_AGENT_STORAGE_KEY = 'ultrarag_enterprise_agent_scope';
export const TASK_PAGE_SIZE = 10;

export const WEEKDAY_OPTIONS = [
  { label: '周一', value: 0 },
  { label: '周二', value: 1 },
  { label: '周三', value: 2 },
  { label: '周四', value: 3 },
  { label: '周五', value: 4 },
  { label: '周六', value: 5 },
  { label: '周日', value: 6 },
];

export type FeishuNotifyConfig = {
  enabled: boolean;
  chat_ids: string[];
  chat_names?: string[];
  webhooks: string[];
  mobiles: string[];
  open_ids?: string[];
  /** 所选飞书应用（ChannelBinding.id）；空串表示"自动"，运行时取最新启用的应用。 */
  binding_id?: string;
  /** 应用快照，仅用于展示；凭证重配后可能过期，禁止用于查找。 */
  app_id?: string;
  app_name?: string;
  // 向下兼容旧字段
  chat_id?: string;
  chat_name?: string;
  webhook_url?: string;
};

export type FeishuAppRead = {
  id: string;
  name?: string | null;
  app_id?: string | null;
  bot_open_id?: string | null;
  bot_name?: string | null;
  status: string;
  is_default?: boolean;
};

export type TaskFormValues = {
  title: string;
  prompt: string;
  description?: string;
  schedule_type: 'once' | 'daily' | 'weekly' | 'monthly' | 'interval';
  interval_minutes?: number;
  time: string;
  run_at: string;
  weekdays: number[];
  day_of_month: number;
  status: 'active' | 'paused';
  max_runs?: number;
  sop_id?: string;
  sop_version_policy: 'latest' | 'pinned';
  feishu_notify: FeishuNotifyConfig;
};

export const INITIAL_VALUES: TaskFormValues = {
  title: '',
  prompt: '',
  description: '',
  schedule_type: 'daily',
  interval_minutes: 1,
  time: '09:00',
  run_at: '',
  weekdays: [0],
  day_of_month: 1,
  status: 'active',
  max_runs: undefined,
  sop_id: '',
  sop_version_policy: 'latest',
  feishu_notify: {
    enabled: false,
    chat_ids: [],
    chat_names: [],
    webhooks: [],
    mobiles: [],
    open_ids: [],
    binding_id: '',
    app_id: '',
    app_name: '',
  },
};

export type TaskListFilter = 'all' | 'pending' | 'completed' | 'paused';
export type RunListFilter = 'all' | 'pending' | 'completed' | 'failed';

export const TASK_FILTER_TABS: UnderlineTabItem<TaskListFilter>[] = [
  { label: '全部', value: 'all' },
  { label: '待完成', value: 'pending' },
  { label: '已完成', value: 'completed' },
  { label: '已暂停', value: 'paused' },
];
export const RUN_FILTER_TABS: UnderlineTabItem<RunListFilter>[] = [
  { label: '全部', value: 'all' },
  { label: '待完成', value: 'pending' },
  { label: '已完成', value: 'completed' },
  { label: '失败/跳过', value: 'failed' },
];

const TASK_FILTERS: Record<TaskListFilter, (row: ScheduledTaskRead) => boolean> = {
  all: () => true,
  pending: (row) => row.status === 'active',
  paused: (row) => row.status === 'paused',
  completed: (row) => row.status === 'completed',
};
const RUN_FILTERS: Record<RunListFilter, (row: ScheduledTaskRunRead) => boolean> = {
  all: () => true,
  pending: (row) =>
    row.status === 'queued' ||
    row.status === 'running' ||
    row.status === 'needs_input' ||
    row.status === 'incomplete',
  failed: (row) => row.status === 'failed' || row.status === 'skipped',
  completed: (row) => row.status === 'succeeded',
};

export function matchesTaskFilter(row: ScheduledTaskRead, filter: TaskListFilter): boolean {
  return TASK_FILTERS[filter](row);
}

export function matchesRunFilter(row: ScheduledTaskRunRead, filter: RunListFilter): boolean {
  return RUN_FILTERS[filter](row);
}

export function scheduledTaskSopOptions<T extends { status: string }>(rows: T[]): T[] {
  // Explicitly selecting an SOP authorizes SOP-specific resources for this
  // scheduled task; only unpublished entries must stay unavailable.
  return rows.filter((row) => row.status === 'published');
}

export type BadgeTone = 'blue' | 'orange' | 'green' | 'red' | 'gray';
export const BADGE_TONE_CLASS: Record<BadgeTone, string> = {
  blue: 'bg-[#e8f0ff] text-[#1a71ff]',
  orange: 'bg-[#fff2e5] text-[#ff7f00]',
  green: 'bg-[#e9f7ef] text-[#2cb360]',
  red: 'bg-[#fce7e7] text-[#d20b0b]',
  gray: 'bg-[#f2f3f7] text-[#858b9c]',
};
export const TASK_STATUS_BADGE: Record<string, { tone: BadgeTone; text: string }> = {
  active: { tone: 'blue', text: '启用' },
  paused: { tone: 'orange', text: '暂停' },
  completed: { tone: 'green', text: '已完成' },
  archived: { tone: 'gray', text: '已删除' },
};
export const RUN_STATUS_BADGE: Record<string, { tone: BadgeTone; text: string }> = {
  succeeded: { tone: 'green', text: '成功' },
  failed: { tone: 'red', text: '失败' },
  running: { tone: 'blue', text: '执行中' },
  needs_input: { tone: 'orange', text: '待补充信息' },
  incomplete: { tone: 'orange', text: '未完成' },
  skipped: { tone: 'gray', text: '已跳过' },
};

const SCHEDULE_TYPES = new Set<TaskFormValues['schedule_type']>(['once', 'daily', 'weekly', 'monthly', 'interval']);
const SCHEDULE_BUILDERS: Record<
  TaskFormValues['schedule_type'],
  (values: TaskFormValues) => Record<string, unknown>
> = {
  interval: (values) => {
    const mins = Math.max(1, Number(values.interval_minutes || 1));
    return {
      interval_minutes: mins,
      interval_seconds: mins * 60,
    };
  },
  once: (values) => ({ run_at: values.run_at }),
  weekly: (values) => ({
    time: values.time || '09:00',
    weekdays: values.weekdays?.length ? values.weekdays : [0],
  }),
  monthly: (values) => ({
    time: values.time || '09:00',
    day_of_month: values.day_of_month || 1,
  }),
  daily: (values) => ({ time: values.time || '09:00' }),
};
const SCHEDULE_FORMATTERS: Record<
  TaskFormValues['schedule_type'],
  (row: ScheduledTaskRead, schedule: Record<string, unknown>) => string
> = {
  interval: (_row, schedule) => {
    const mins = schedule.interval_minutes || Math.round(Number(schedule.interval_seconds || 60) / 60) || 1;
    return `每 ${mins} 分钟`;
  },
  once: (row, schedule) => `一次性 · ${formatTime(String(schedule.run_at || row.next_run_at || ''))}`,
  weekly: (_row, schedule) => {
    const days = Array.isArray(schedule.weekdays)
      ? schedule.weekdays
          .map((item) => WEEKDAY_OPTIONS[Number(item)]?.label)
          .filter(Boolean)
          .join('、')
      : '周一';
    return `每周 ${days} ${schedule.time || '09:00'}`;
  },
  monthly: (_row, schedule) => `每月 ${schedule.day_of_month || 1} 号 ${schedule.time || '09:00'}`,
  daily: (_row, schedule) => `每天 ${schedule.time || '09:00'}`,
};

export function buildSchedule(values: TaskFormValues): Record<string, unknown> {
  return SCHEDULE_BUILDERS[values.schedule_type](values);
}

export function taskToFormValues(row: ScheduledTaskRead): TaskFormValues {
  const schedule = row.schedule || {};
  const mins = schedule.interval_minutes || Math.round(Number(schedule.interval_seconds || 60) / 60) || 1;
  const feishuNotify = (row.metadata?.feishu_notify || {}) as Partial<FeishuNotifyConfig> & {
    chat_id?: string;
    chat_name?: string;
    webhook_url?: string;
  };

  const chatIds = Array.isArray(feishuNotify.chat_ids)
    ? feishuNotify.chat_ids.map(String).filter(Boolean)
    : (feishuNotify.chat_id ? [String(feishuNotify.chat_id)] : []);
  const chatNames = Array.isArray(feishuNotify.chat_names)
    ? feishuNotify.chat_names.map(String)
    : (feishuNotify.chat_name ? [String(feishuNotify.chat_name)] : []);
  const webhooks = Array.isArray(feishuNotify.webhooks)
    ? feishuNotify.webhooks.map(String).filter(Boolean)
    : (feishuNotify.webhook_url ? [String(feishuNotify.webhook_url)] : []);
  const mobiles = Array.isArray(feishuNotify.mobiles)
    ? feishuNotify.mobiles.map(String).filter(Boolean)
    : [];
  const openIds = Array.isArray(feishuNotify.open_ids)
    ? feishuNotify.open_ids.map(String).filter(Boolean)
    : [];

  return {
    title: row.title,
    prompt: row.prompt,
    description: row.description || '',
    schedule_type: normalizeScheduleType(row.schedule_type),
    interval_minutes: Number(mins),
    time: String(schedule.time || '09:00'),
    run_at: toDatetimeLocal(String(schedule.run_at || row.next_run_at || '')),
    weekdays: Array.isArray(schedule.weekdays) ? schedule.weekdays.map((item) => Number(item)) : [0],
    day_of_month: Number(schedule.day_of_month || 1),
    status: row.status === 'active' ? 'active' : 'paused',
    max_runs: row.max_runs,
    sop_id: typeof row.metadata?.sop_id === 'string' ? row.metadata.sop_id : '',
    sop_version_policy: row.metadata?.sop_version_policy === 'pinned' ? 'pinned' : 'latest',
    feishu_notify: {
      enabled: Boolean(feishuNotify.enabled),
      chat_ids: chatIds,
      chat_names: chatNames,
      webhooks,
      mobiles,
      open_ids: openIds,
      binding_id: asText(feishuNotify.binding_id),
      app_id: asText(feishuNotify.app_id),
      app_name: asText(feishuNotify.app_name),
      chat_id: chatIds[0] || '',
      chat_name: chatNames[0] || '',
    },
  };
}

function asText(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

/**
 * 切换推送所使用的飞书应用。
 *
 * 群聊 ID 是应用维度的标识：A 应用拿到的 chat_id 对 B 应用不可寻址，因此必须清空；
 * 手机号与个人 OpenID 由所选应用在运行时反查通讯录或直接私聊、Webhook 走独立 HTTP，均与应用解耦，予以保留。
 */
export function switchFeishuApp(
  notify: FeishuNotifyConfig,
  app: FeishuAppRead | null,
): FeishuNotifyConfig {
  return {
    ...notify,
    binding_id: app?.id || '',
    app_id: app?.app_id || '',
    app_name: app?.name || app?.bot_name || '',
    chat_ids: [],
    chat_names: [],
    chat_id: '',
    chat_name: '',
    mobiles: notify.mobiles || [],
    open_ids: notify.open_ids || [],
  };
}

export function normalizeScheduleType(value: string): TaskFormValues['schedule_type'] {
  const scheduleType = value as TaskFormValues['schedule_type'];
  return SCHEDULE_TYPES.has(scheduleType) ? scheduleType : 'daily';
}

export function toDatetimeLocal(value: string): string {
  if (!value) return '';
  const date = parseBackendDateTime(value);
  if (Number.isNaN(date.getTime())) return '';
  const offset = date.getTimezoneOffset();
  const local = new Date(date.getTime() - offset * 60000);
  return local.toISOString().slice(0, 16);
}

export function formatSchedule(row: ScheduledTaskRead): string {
  const schedule = row.schedule || {};
  return SCHEDULE_FORMATTERS[normalizeScheduleType(row.schedule_type)](row, schedule);
}

export function formatTime(value?: string): string {
  return formatClientDateTime(value, '暂无');
}
