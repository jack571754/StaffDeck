// @vitest-environment jsdom

import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '@/i18n';
import type { FeishuOutboundMessagePage, FeishuOutboundStatsRead } from '@/types';
import FeishuMessagesTab from './FeishuMessagesTab';

const mockStats: FeishuOutboundStatsRead = {
  total_today: 12,
  delivered_today: 10,
  failed_today: 1,
  recalled_today: 1,
};

const mockMessages: FeishuOutboundMessagePage = {
  items: [
    {
      id: 'fsmsg-1',
      tenant_id: 'tenant_demo',
      channel_type: 'app_bot',
      feishu_message_id: 'om_1234567890',
      target_type: 'chat_id',
      target_identifier: 'oc_sales_room',
      target_name: '线上销售播报群',
      title: '实时销售播报（今日第 1 期）',
      card_json: {
        header: { title: { content: '实时销售播报' } },
        elements: [{ tag: 'div', text: { content: '今日大盘销售达成' } }],
      },
      status: 'delivered',
      can_recall: true,
      created_at: '2026-09-25T08:00:00Z',
      updated_at: '2026-09-25T08:00:00Z',
    },
    {
      id: 'fsmsg-2',
      tenant_id: 'tenant_demo',
      channel_type: 'webhook',
      target_type: 'webhook',
      target_identifier: 'https://open.feishu.cn/open-apis/bot/v2/hook/abc',
      target_name: '价格监控群',
      title: '商品降价预警',
      card_json: {},
      status: 'delivered',
      can_recall: false,
      created_at: '2026-09-25T07:30:00Z',
      updated_at: '2026-09-25T07:30:00Z',
    },
  ],
  total: 2,
  offset: 0,
  limit: 10,
};

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    text: async () => JSON.stringify(body ?? {}),
  } as Response;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('FeishuMessagesTab', () => {
  it('renders stats, table data and allows recalling app_bot messages', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method || 'GET';

      if (url.includes('/channels/feishu/messages/stats')) {
        return jsonResponse(mockStats);
      }
      if (method === 'POST' && url.includes('/channels/feishu/messages/fsmsg-1/recall')) {
        return jsonResponse({ ok: true, recalled: true });
      }
      if (url.includes('/channels/feishu/messages')) {
        return jsonResponse(mockMessages);
      }
      return jsonResponse({});
    });
    vi.stubGlobal('fetch', fetchMock);

    render(
      <I18nProvider>
        <FeishuMessagesTab />
      </I18nProvider>,
    );

    // Check stats cards
    await screen.findByText('今日推送总数');
    expect(screen.getByText('12')).toBeTruthy();
    expect(screen.getByText('10')).toBeTruthy();

    // Check table rows
    await screen.findByText('实时销售播报（今日第 1 期）');
    expect(screen.getByText('线上销售播报群')).toBeTruthy();
    expect(screen.getByText('价格监控群')).toBeTruthy();
    expect(screen.getByText('自建应用')).toBeTruthy();
    expect(screen.getByText('群 Webhook')).toBeTruthy();

    // Click recall on the first message
    const recallButtons = screen.getAllByRole('button', { name: '撤回' });
    expect((recallButtons[0] as HTMLButtonElement).disabled).toBe(false);
    // Second button (webhook) should be disabled
    expect((recallButtons[1] as HTMLButtonElement).disabled).toBe(true);

    // Trigger recall
    await user.click(recallButtons[0]);
    await screen.findByText('确定撤回该飞书消息？');
    const confirmBtn = screen.getByRole('button', { name: '确认撤回' });
    await user.click(confirmBtn);

    await waitFor(() => {
      const recallCall = fetchMock.mock.calls.find(
        ([callUrl, callInit]) =>
          callInit?.method === 'POST' && String(callUrl).includes('/fsmsg-1/recall'),
      );
      expect(recallCall).toBeTruthy();
    });
  });

  it('opens message card detail dialog when clicking details', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/channels/feishu/messages/stats')) {
        return jsonResponse(mockStats);
      }
      if (url.includes('/channels/feishu/messages')) {
        return jsonResponse(mockMessages);
      }
      return jsonResponse({});
    });
    vi.stubGlobal('fetch', fetchMock);

    render(
      <I18nProvider>
        <FeishuMessagesTab />
      </I18nProvider>,
    );

    await screen.findByText('实时销售播报（今日第 1 期）');
    const detailBtns = screen.getAllByRole('button', { name: '卡片详情' });
    await user.click(detailBtns[0]);

    await screen.findByText('飞书推送消息详情');
    expect(screen.getByText('卡片报文 (Card Payload)')).toBeTruthy();
    expect(screen.getByText('om_1234567890')).toBeTruthy();
  });
});
