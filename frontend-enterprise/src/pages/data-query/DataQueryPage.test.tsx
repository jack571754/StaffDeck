// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

import { I18nProvider } from '@/i18n';

import DataQueryPage from './DataQueryPage';
import type { EnterpriseAuthUser } from '@/auth';

const admin: EnterpriseAuthUser = {
  id: 'user_admin',
  tenant_id: 'tenant_demo',
  username: 'admin',
  role: 'admin',
};

const member: EnterpriseAuthUser = { ...admin, id: 'user_member', role: 'member' };

function stubRadixPointerApis() {
  if (!window.ResizeObserver) {
    window.ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    } as unknown as typeof ResizeObserver;
  }
  if (!window.matchMedia) {
    window.matchMedia = ((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    })) as typeof window.matchMedia;
  }
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = () => false;
  }
  if (!Element.prototype.releasePointerCapture) {
    Element.prototype.releasePointerCapture = () => {};
  }
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = () => {};
  }
}

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? 'OK' : 'Error',
    text: async () => JSON.stringify(body ?? {}),
    json: async () => body,
  } as Response;
}

function uiConfigPayload(grantAll: boolean) {
  return {
    tenant_id: 'tenant_demo',
    show_thinking_trace: true,
    show_skill_trace: true,
    show_tool_trace: true,
    reflection_max_rounds: 1,
    agent_loop_max_actions: 32,
    context_token_budget: 32000,
    context_compaction_trigger_ratio: 0.7,
    context_recent_round_limit: 6,
    context_long_summary_token_budget: 2000,
    context_medium_summary_token_budget: 1000,
    context_allowed_roles: ['user', 'assistant'],
    context_long_summary_prefix: '长期：',
    context_medium_summary_prefix: '近期：',
    sandbox_enabled: false,
    data_query_grant_all: grantAll,
    harness_storage_path: '',
    effective_harness_storage_path: '',
    updated_at: '2026-09-01T00:00:00Z',
  };
}

beforeEach(() => {
  stubRadixPointerApis();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderPage(currentUser: EnterpriseAuthUser, grantAll = false) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes('/api/enterprise/ui-config')) {
      if (init?.method === 'PUT') {
        return jsonResponse(uiConfigPayload(JSON.parse(String(init.body)).data_query_grant_all));
      }
      return jsonResponse(uiConfigPayload(grantAll));
    }
    return jsonResponse([]);
  });
  vi.stubGlobal('fetch', fetchMock);

  render(
    <MemoryRouter initialEntries={['/enterprise/data-query']}>
      <I18nProvider>
        <DataQueryPage currentUser={currentUser} onLogout={() => {}} />
      </I18nProvider>
    </MemoryRouter>,
  );
  return fetchMock;
}

describe('DataQueryPage 租户授权开关', () => {
  it('admin 可见「默认授权所有员工」开关并可切换，PUT 仅携带 tenant_id 与开关值', async () => {
    const user = userEvent.setup();
    const fetchMock = renderPage(admin, false);

    const toggle = await screen.findByRole('switch', { name: /默认授权所有员工/ });
    expect(toggle.getAttribute('aria-checked')).toBe('false');

    await user.click(toggle);

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([input, init]) => String(input).includes('/ui-config') && init?.method === 'PUT'),
      ).toBe(true);
    });
    const putCall = fetchMock.mock.calls.find(
      ([input, init]) => String(input).includes('/ui-config') && init?.method === 'PUT',
    )!;
    const payload = JSON.parse(String(putCall[1]?.body));
    expect(Object.keys(payload).sort()).toEqual(['data_query_grant_all', 'tenant_id']);
    expect(payload.data_query_grant_all).toBe(true);
  });

  it('非 admin 的开关只读不可切换', async () => {
    const fetchMock = renderPage(member, false);

    const toggle = await screen.findByRole('switch', { name: /默认授权所有员工/ });
    expect((toggle as HTMLButtonElement).disabled).toBe(true);
    expect(
      fetchMock.mock.calls.some(([input, init]) => String(input).includes('/ui-config') && init?.method === 'PUT'),
    ).toBe(false);
  });

  it('开启后说明文案切换为例外收紧语义', async () => {
    const user = userEvent.setup();
    renderPage(admin, false);

    expect(await screen.findByText(/按员工档案勾选白名单授权/)).toBeTruthy();
    await user.click(screen.getByRole('switch', { name: /默认授权所有员工/ }));

    await screen.findByText(/表示排除/);
  });
});
