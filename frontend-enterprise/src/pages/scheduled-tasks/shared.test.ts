import { describe, expect, it } from 'vitest';

import type { ScheduledTaskRead, ScheduledTaskRunRead } from '../../types';
import {
  matchesRunFilter,
  RUN_STATUS_BADGE,
  scheduledTaskSopOptions,
  switchFeishuApp,
  taskToFormValues,
} from './shared';

/** Minimal scheduled task carrying a feishu_notify metadata blob. */
function taskWithNotify(feishuNotify: Record<string, unknown>): ScheduledTaskRead {
  return {
    id: 'scheduled-feishu',
    tenant_id: 'tenant-demo',
    agent_id: 'agent-1',
    created_by_user_id: 'user-1',
    title: '飞书通知任务',
    prompt: '执行飞书通知',
    schedule_type: 'daily',
    schedule: { time: '09:00' },
    timezone: 'Asia/Shanghai',
    status: 'active',
    concurrency_policy: 'forbid',
    misfire_policy: 'coalesce',
    run_count: 0,
    metadata: { feishu_notify: feishuNotify },
    created_at: '2026-08-01T09:00:00',
    updated_at: '2026-08-01T09:00:00',
  } satisfies ScheduledTaskRead;
}

function run(status: string): ScheduledTaskRunRead {
  return {
    id: `run-${status}`,
    tenant_id: 'tenant-demo',
    scheduled_task_id: 'scheduled-1',
    agent_id: 'agent-1',
    user_id: 'user-1',
    scheduled_for: '2026-08-01T09:00:00',
    status,
    trace: {},
    created_at: '2026-08-01T09:00:00',
    updated_at: '2026-08-01T09:00:00',
  };
}

describe('scheduled task Harness statuses', () => {
  it.each(['queued', 'running', 'needs_input', 'incomplete'])(
    'keeps %s in the pending filter',
    (status) => {
      expect(matchesRunFilter(run(status), 'pending')).toBe(true);
    },
  );

  it('presents non-terminal Harness outcomes explicitly', () => {
    expect(RUN_STATUS_BADGE.needs_input.text).toBe('待补充信息');
    expect(RUN_STATUS_BADGE.incomplete.text).toBe('未完成');
  });
});

describe('scheduled task SOP selection', () => {
  it('allows explicitly selected SOP-specific SOPs while excluding drafts', () => {
    const rows = [
      { id: 'general', status: 'published', capability_scope: 'general' },
      { id: 'specific', status: 'published', capability_scope: 'sop_specific' },
      { id: 'draft', status: 'draft', capability_scope: 'general' },
    ];

    expect(scheduledTaskSopOptions(rows).map((row) => row.id)).toEqual([
      'general',
      'specific',
    ]);
  });

  it('restores the pinned Harness v2 SOP from task metadata', () => {
    const task = {
      id: 'scheduled-1',
      tenant_id: 'tenant-demo',
      agent_id: 'agent-1',
      created_by_user_id: 'user-1',
      title: '日报',
      prompt: '生成日报',
      schedule_type: 'daily',
      schedule: { time: '09:00' },
      timezone: 'Asia/Shanghai',
      status: 'active',
      concurrency_policy: 'forbid',
      misfire_policy: 'coalesce',
      run_count: 0,
      metadata: {
        sop_id: 'daily_report_v2',
        sop_version_policy: 'pinned',
        sop_version: '1.0.0',
      },
      created_at: '2026-08-01T09:00:00',
      updated_at: '2026-08-01T09:00:00',
    } satisfies ScheduledTaskRead;

    expect(taskToFormValues(task).sop_id).toBe('daily_report_v2');
    expect(taskToFormValues(task).sop_version_policy).toBe('pinned');
  });

  it('defaults existing tasks to the latest published SOP policy', () => {
    const task = {
      id: 'scheduled-2',
      tenant_id: 'tenant-demo',
      agent_id: 'agent-1',
      created_by_user_id: 'user-1',
      title: '日报',
      prompt: '生成日报',
      schedule_type: 'daily',
      schedule: { time: '09:00' },
      timezone: 'Asia/Shanghai',
      status: 'active',
      concurrency_policy: 'forbid',
      misfire_policy: 'coalesce',
      run_count: 0,
      metadata: { sop_id: 'daily_report_v2' },
      created_at: '2026-08-01T09:00:00',
      updated_at: '2026-08-01T09:00:00',
    } satisfies ScheduledTaskRead;

    expect(taskToFormValues(task).sop_version_policy).toBe('latest');
  });

  it('restores multi-target feishu notify configuration from metadata', () => {
    const task = {
      id: 'scheduled-notify-multi',
      tenant_id: 'tenant-demo',
      agent_id: 'agent-1',
      created_by_user_id: 'user-1',
      title: '销售播报',
      prompt: '执行销售播报',
      schedule_type: 'daily',
      schedule: { time: '09:00' },
      timezone: 'Asia/Shanghai',
      status: 'active',
      concurrency_policy: 'forbid',
      misfire_policy: 'coalesce',
      run_count: 0,
      metadata: {
        feishu_notify: {
          enabled: true,
          chat_ids: ['oc_group_1', 'oc_group_2'],
          chat_names: ['价控小组', '战报群'],
          webhooks: ['https://open.feishu.cn/bot/1', 'https://open.feishu.cn/bot/2'],
          mobiles: ['13800138000', '13900139000'],
        },
      },
      created_at: '2026-08-01T09:00:00',
      updated_at: '2026-08-01T09:00:00',
    } satisfies ScheduledTaskRead;

    const values = taskToFormValues(task);
    expect(values.feishu_notify.enabled).toBe(true);
    expect(values.feishu_notify.chat_ids).toEqual(['oc_group_1', 'oc_group_2']);
    expect(values.feishu_notify.chat_names).toEqual(['价控小组', '战报群']);
    expect(values.feishu_notify.webhooks).toEqual(['https://open.feishu.cn/bot/1', 'https://open.feishu.cn/bot/2']);
    expect(values.feishu_notify.mobiles).toEqual(['13800138000', '13900139000']);
  });

  it('smoothly migrates legacy single chat_id and webhook_url into arrays', () => {
    const task = {
      id: 'scheduled-notify-legacy',
      tenant_id: 'tenant-demo',
      agent_id: 'agent-1',
      created_by_user_id: 'user-1',
      title: '旧版播报',
      prompt: '执行旧版播报',
      schedule_type: 'daily',
      schedule: { time: '09:00' },
      timezone: 'Asia/Shanghai',
      status: 'active',
      concurrency_policy: 'forbid',
      misfire_policy: 'coalesce',
      run_count: 0,
      metadata: {
        feishu_notify: {
          enabled: true,
          chat_id: 'oc_legacy_group',
          chat_name: '旧群',
          webhook_url: 'https://open.feishu.cn/bot/legacy_hook',
          mobiles: ['13800000000'],
        },
      },
      created_at: '2026-08-01T09:00:00',
      updated_at: '2026-08-01T09:00:00',
    } satisfies ScheduledTaskRead;

    const values = taskToFormValues(task);
    expect(values.feishu_notify.chat_ids).toEqual(['oc_legacy_group']);
    expect(values.feishu_notify.webhooks).toEqual(['https://open.feishu.cn/bot/legacy_hook']);
    expect(values.feishu_notify.mobiles).toEqual(['13800000000']);
  });

  it('restores the selected feishu app from metadata', () => {
    const values = taskToFormValues(taskWithNotify({ binding_id: 'bind-2', app_id: 'cli_b', app_name: '价控应用' }));

    expect(values.feishu_notify.binding_id).toBe('bind-2');
    expect(values.feishu_notify.app_id).toBe('cli_b');
    expect(values.feishu_notify.app_name).toBe('价控应用');
  });

  it('keeps auto mode for tasks saved before app selection existed', () => {
    const values = taskToFormValues(taskWithNotify({ chat_ids: ['oc_group_1'] }));

    expect(values.feishu_notify.binding_id).toBe('');
    expect(values.feishu_notify.app_id).toBe('');
    expect(values.feishu_notify.app_name).toBe('');
  });

  it('tolerates non-string app fields in free-form metadata', () => {
    const values = taskToFormValues(
      taskWithNotify({ binding_id: 42, app_id: null, app_name: { nested: true } }),
    );

    expect(values.feishu_notify.binding_id).toBe('');
    expect(values.feishu_notify.app_id).toBe('');
    expect(values.feishu_notify.app_name).toBe('');
  });

  it('switchFeishuApp clears app-scoped chats but keeps mobiles and webhooks', () => {
    const next = switchFeishuApp(
      {
        enabled: true,
        chat_ids: ['oc_from_app_a'],
        chat_names: ['A 群'],
        chat_id: 'oc_from_app_a',
        chat_name: 'A 群',
        webhooks: ['https://open.feishu.cn/bot/1'],
        mobiles: ['13800138000'],
        binding_id: 'bind-1',
        app_id: 'cli_a',
        app_name: 'A 应用',
      },
      { id: 'bind-2', name: 'B 应用', app_id: 'cli_b', status: 'active' },
    );

    // chat_id 是应用维度标识：A 应用拿到的 oc_xxx 对 B 应用不可寻址，必须清空。
    expect(next.chat_ids).toEqual([]);
    expect(next.chat_names).toEqual([]);
    expect(next.chat_id).toBe('');
    expect(next.chat_name).toBe('');
    // 手机号由所选应用运行时反查、Webhook 走独立 HTTP，均与应用解耦。
    expect(next.mobiles).toEqual(['13800138000']);
    expect(next.webhooks).toEqual(['https://open.feishu.cn/bot/1']);
    expect(next.binding_id).toBe('bind-2');
    expect(next.app_id).toBe('cli_b');
    expect(next.app_name).toBe('B 应用');
    expect(next.enabled).toBe(true);
  });

  it('switchFeishuApp to auto clears the app snapshot and app-scoped chats', () => {
    const next = switchFeishuApp(
      {
        enabled: true,
        chat_ids: ['oc_group_1'],
        chat_names: ['群一'],
        webhooks: [],
        mobiles: ['13800138000'],
        binding_id: 'bind-1',
        app_id: 'cli_a',
        app_name: 'A 应用',
      },
      null,
    );

    expect(next.binding_id).toBe('');
    expect(next.app_id).toBe('');
    expect(next.app_name).toBe('');
    expect(next.chat_ids).toEqual([]);
    expect(next.mobiles).toEqual(['13800138000']);
  });
});
