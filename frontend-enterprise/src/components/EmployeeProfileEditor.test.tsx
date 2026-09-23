// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { DataSource } from '@/api/data-query';
import { I18nProvider } from '@/i18n';

import EmployeeProfileEditor from './EmployeeProfileEditor';
import type { AgentProfileRead } from '../types';

const mockDataSources: DataSource[] = [
  {
    id: 'ds_sales',
    tenant_id: 'tenant_demo',
    name: '销售数据库',
    description: '主业务 MySQL',
    type: 'mysql',
    read_only: true,
    status: 'active',
    last_test_at: null,
    created_at: '2026-09-01T00:00:00Z',
    updated_at: '2026-09-01T00:00:00Z',
  },
  {
    id: 'ds_erp',
    tenant_id: 'tenant_demo',
    name: 'ERP 库',
    description: '',
    type: 'postgres',
    read_only: true,
    status: 'active',
    last_test_at: null,
    created_at: '2026-09-01T00:00:00Z',
    updated_at: '2026-09-01T00:00:00Z',
  },
];

function makeAgent(overrides: Partial<AgentProfileRead> = {}): AgentProfileRead {
  return {
    id: 'agent_1',
    tenant_id: 'tenant_demo',
    name: '数据员工',
    description: '',
    is_overall: false,
    status: 'active',
    harness_max_actions: 32,
    metadata: {},
    resources: [],
    created_at: '2026-09-01T00:00:00Z',
    updated_at: '2026-09-01T00:00:00Z',
    ...overrides,
  };
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

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderEditor(agent: AgentProfileRead, onSaved?: () => void) {
  return render(
    <I18nProvider>
      <EmployeeProfileEditor agent={agent} open onClose={() => {}} onSaved={onSaved} />
    </I18nProvider>,
  );
}

describe('EmployeeProfileEditor 数据权限区', () => {
  it('展示租户数据源列表与类型徽标，并回显已绑定勾选态', async () => {
    const agent = makeAgent({
      resources: [
        {
          id: 'bind_1',
          tenant_id: 'tenant_demo',
          agent_id: 'agent_1',
          resource_type: 'data_source',
          resource_id: 'ds_sales',
          status: 'active',
          metadata: {},
          created_at: '2026-09-01T00:00:00Z',
          updated_at: '2026-09-01T00:00:00Z',
        },
      ],
    });
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/data-query/data-sources')) return jsonResponse(mockDataSources);
      return jsonResponse({});
    });
    vi.stubGlobal('fetch', fetchMock);

    renderEditor(agent);

    expect(await screen.findByText('销售数据库')).toBeTruthy();
    expect(screen.getByText('ERP 库')).toBeTruthy();
    expect(screen.getByText('mysql')).toBeTruthy();
    const salesCheckbox = screen.getByRole('checkbox', { name: /销售数据库/ });
    expect(salesCheckbox.getAttribute('aria-checked')).toBe('true');
    const erpCheckbox = screen.getByRole('checkbox', { name: /ERP 库/ });
    expect(erpCheckbox.getAttribute('aria-checked')).toBe('false');
  });

  it('保存时调用 resources API，携带其他类型绑定原样与勾选的数据源', async () => {
    const agent = makeAgent({
      resources: [
        {
          id: 'bind_tool',
          tenant_id: 'tenant_demo',
          agent_id: 'agent_1',
          resource_type: 'tool',
          resource_id: 'tool_1',
          status: 'active',
          metadata: {},
          created_at: '2026-09-01T00:00:00Z',
          updated_at: '2026-09-01T00:00:00Z',
        },
      ],
    });
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/data-query/data-sources')) return jsonResponse(mockDataSources);
      if (url.includes('/resources')) return jsonResponse([]);
      return jsonResponse(agent);
    });
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup();
    renderEditor(agent);

    await screen.findByText('销售数据库');
    await user.click(screen.getByRole('checkbox', { name: /销售数据库/ }));
    await user.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => {
      const resourcesCall = fetchMock.mock.calls.find(
        ([input]) => String(input).includes('/agents/agent_1/resources'),
      );
      expect(resourcesCall).toBeTruthy();
    });
    const resourcesCall = fetchMock.mock.calls.find(
      ([input]) => String(input).includes('/agents/agent_1/resources'),
    )!;
    expect(String(resourcesCall[0])).toContain('/api/enterprise/agents/agent_1/resources');
    const payload = JSON.parse((resourcesCall[1] as RequestInit).body as string);
    const types = payload.resources.map(
      (row: { resource_type: string; resource_id: string }) => `${row.resource_type}:${row.resource_id}`,
    );
    expect(types).toContain('tool:tool_1');
    expect(types).toContain('data_source:ds_sales');
    expect(types.filter((row: string) => row.startsWith('data_source'))).toHaveLength(1);
  });

  it('取消勾选已绑定数据源后保存，resources payload 中不再包含该数据源', async () => {
    const agent = makeAgent({
      resources: [
        {
          id: 'bind_1',
          tenant_id: 'tenant_demo',
          agent_id: 'agent_1',
          resource_type: 'data_source',
          resource_id: 'ds_sales',
          status: 'active',
          metadata: {},
          created_at: '2026-09-01T00:00:00Z',
          updated_at: '2026-09-01T00:00:00Z',
        },
      ],
    });
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/data-query/data-sources')) return jsonResponse(mockDataSources);
      if (url.includes('/resources')) return jsonResponse([]);
      return jsonResponse(agent);
    });
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup();
    renderEditor(agent);

    const salesCheckbox = await screen.findByRole('checkbox', { name: /销售数据库/ });
    expect(salesCheckbox.getAttribute('aria-checked')).toBe('true');
    await user.click(salesCheckbox);
    await user.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => {
      const resourcesCall = fetchMock.mock.calls.find(
        ([input]) => String(input).includes('/agents/agent_1/resources'),
      );
      expect(resourcesCall).toBeTruthy();
    });
    const resourcesCall = fetchMock.mock.calls.find(
      ([input]) => String(input).includes('/agents/agent_1/resources'),
    )!;
    const payload = JSON.parse((resourcesCall[1] as RequestInit).body as string);
    const dataSourceIds = payload.resources
      .filter((row: { resource_type: string }) => row.resource_type === 'data_source')
      .map((row: { resource_id: string }) => row.resource_id);
    expect(dataSourceIds).toEqual([]);
  });

  it('总体员工（is_overall）不显示数据权限区', () => {
    const agent = makeAgent({ is_overall: true });
    const fetchMock = vi.fn(async () => jsonResponse([]));
    vi.stubGlobal('fetch', fetchMock);

    renderEditor(agent);

    expect(screen.queryByText('数据权限')).toBeNull();
  });
});

describe('EmployeeProfileEditor 数据权限双模式', () => {
  const inactiveBinding = {
    id: 'bind_2',
    tenant_id: 'tenant_demo',
    agent_id: 'agent_1',
    resource_type: 'data_source' as const,
    resource_id: 'ds_sales',
    status: 'inactive',
    metadata: {},
    created_at: '2026-09-01T00:00:00Z',
    updated_at: '2026-09-01T00:00:00Z',
  };

  it('grant_all 开启时，inactive 绑定回显为勾选（排除集合）且文案为例外语义', async () => {
    const agent = makeAgent({ resources: [inactiveBinding] });
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/data-query/data-sources')) return jsonResponse(mockDataSources);
      if (url.includes('/ui-config')) return jsonResponse({ tenant_id: 'tenant_demo', data_query_grant_all: true });
      return jsonResponse({});
    });
    vi.stubGlobal('fetch', fetchMock);

    renderEditor(agent);

    const salesCheckbox = await screen.findByRole('checkbox', { name: /销售数据库/ });
    expect(salesCheckbox.getAttribute('aria-checked')).toBe('true');
    const erpCheckbox = screen.getByRole('checkbox', { name: /ERP 库/ });
    expect(erpCheckbox.getAttribute('aria-checked')).toBe('false');
    expect(screen.getByText(/数据权限（排除）/)).toBeTruthy();
  });

  it('grant_all 开启时保存，勾选项写 inactive 排除绑定，未勾选不发 data_source 行', async () => {
    const agent = makeAgent({ resources: [] });
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/data-query/data-sources')) return jsonResponse(mockDataSources);
      if (url.includes('/ui-config')) return jsonResponse({ tenant_id: 'tenant_demo', data_query_grant_all: true });
      if (url.includes('/resources')) return jsonResponse([]);
      return jsonResponse(agent);
    });
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup();
    renderEditor(agent);

    const salesCheckbox = await screen.findByRole('checkbox', { name: /销售数据库/ });
    await user.click(salesCheckbox);
    await user.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([input, init]) => String(input).includes('/resources') && init?.method === 'PUT')).toBe(true);
    });
    const resourcesCall = fetchMock.mock.calls.find(
      ([input, init]) => String(input).includes('/resources') && init?.method === 'PUT',
    )!;
    const payload = JSON.parse(String((resourcesCall[1] as RequestInit).body));
    const dataSourceRows = payload.resources.filter(
      (row: { resource_type: string }) => row.resource_type === 'data_source',
    );
    expect(dataSourceRows).toHaveLength(1);
    expect(dataSourceRows[0]).toMatchObject({ resource_type: 'data_source', resource_id: 'ds_sales', status: 'inactive' });
  });

  it('ui-config 加载失败回退白名单模式：inactive 绑定不勾选', async () => {
    const agent = makeAgent({ resources: [inactiveBinding] });
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/data-query/data-sources')) return jsonResponse(mockDataSources);
      if (url.includes('/ui-config')) return jsonResponse({}, 500);
      return jsonResponse({});
    });
    vi.stubGlobal('fetch', fetchMock);

    renderEditor(agent);

    const salesCheckbox = await screen.findByRole('checkbox', { name: /销售数据库/ });
    await waitFor(() => {
      expect(salesCheckbox.getAttribute('aria-checked')).toBe('false');
    });
    expect(screen.queryByText(/数据权限（排除）/)).toBeNull();
  });
});
