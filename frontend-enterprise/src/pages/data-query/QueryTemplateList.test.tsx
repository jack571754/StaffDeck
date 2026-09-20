// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '@/i18n';
import type { DataSource, QueryTemplate } from '@/api/data-query';

import QueryTemplateList from './QueryTemplateList';

const mockDataSources: DataSource[] = [
  {
    id: 'ds-1',
    tenant_id: 'tenant_demo',
    name: '生产数据库',
    description: '主业务 MySQL 数据库',
    type: 'mysql',
    read_only: true,
    status: 'active',
    last_test_at: '2026-09-15T10:30:00Z',
    created_at: '2026-09-01T00:00:00Z',
    updated_at: '2026-09-15T10:30:00Z',
  },
  {
    id: 'ds-2',
    tenant_id: 'tenant_demo',
    name: '外部 API',
    description: '第三方数据接口',
    type: 'http_api',
    read_only: false,
    status: 'error',
    last_test_at: null,
    created_at: '2026-09-10T00:00:00Z',
    updated_at: '2026-09-12T00:00:00Z',
  },
];

const mockTemplates: QueryTemplate[] = [
  {
    id: 'tpl-1',
    tenant_id: 'tenant_demo',
    name: '用户订单查询',
    description: '按用户 ID 查询订单列表',
    data_source_id: 'ds-1',
    query_type: 'sql',
    query_content: 'SELECT * FROM orders WHERE user_id = :user_id',
    params_json: [],
    output_config_json: {},
    cache_ttl: 300,
    timeout_seconds: 30,
    max_rows: 1000,
    status: 'active',
    created_at: '2026-09-05T00:00:00Z',
    updated_at: '2026-09-10T00:00:00Z',
  },
  {
    id: 'tpl-2',
    tenant_id: 'tenant_demo',
    name: '天气数据获取',
    description: '获取指定城市的天气信息',
    data_source_id: 'ds-2',
    query_type: 'http',
    query_content: 'GET /api/weather?city={city}',
    params_json: [],
    output_config_json: {},
    cache_ttl: 60,
    timeout_seconds: 10,
    max_rows: 100,
    status: 'draft',
    created_at: '2026-09-12T00:00:00Z',
    updated_at: '2026-09-12T00:00:00Z',
  },
];

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

function renderComponent() {
  return render(
    <I18nProvider>
      <MemoryRouter>
        <QueryTemplateList />
      </MemoryRouter>
    </I18nProvider>,
  );
}

describe('QueryTemplateList', () => {
  it('renders loading state initially', () => {
    const fetchMock = vi.fn(() => new Promise<Response>(() => {}));
    vi.stubGlobal('fetch', fetchMock);

    renderComponent();

    expect(screen.getByText('查询模板')).toBeTruthy();
    expect(screen.getByLabelText('查询模板列表')).toBeTruthy();
  });

  it('renders empty state when no templates exist', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/data-sources')) return jsonResponse(mockDataSources);
      return jsonResponse([]);
    });
    vi.stubGlobal('fetch', fetchMock);

    renderComponent();

    expect(await screen.findByText('暂无查询模板，点击「新建模板」创建一个吧')).toBeTruthy();
    expect(screen.getByText('共 0 个')).toBeTruthy();
  });

  it('renders query template list with mock data', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/data-sources')) return jsonResponse(mockDataSources);
      return jsonResponse(mockTemplates);
    });
    vi.stubGlobal('fetch', fetchMock);

    renderComponent();

    expect(await screen.findByText('用户订单查询')).toBeTruthy();
    expect(screen.getByText('天气数据获取')).toBeTruthy();
    expect(screen.getByText('共 2 个')).toBeTruthy();

    // 描述显示
    expect(screen.getByText('按用户 ID 查询订单列表')).toBeTruthy();
    expect(screen.getByText('获取指定城市的天气信息')).toBeTruthy();
  });

  it('shows correct type labels for SQL and HTTP', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/data-sources')) return jsonResponse(mockDataSources);
      return jsonResponse(mockTemplates);
    });
    vi.stubGlobal('fetch', fetchMock);

    renderComponent();

    await screen.findByText('用户订单查询');

    // SQL 类型标签 - 蓝色 (bg-[#e8f0ff] text-[#1a71ff])
    const sqlBadge = screen.getByText('SQL');
    expect(sqlBadge).toBeTruthy();
    expect(sqlBadge.className).toContain('bg-[#e8f0ff]');
    expect(sqlBadge.className).toContain('text-[#1a71ff]');

    // HTTP 类型标签 - 橙色 (bg-[#fff2e5] text-[#ff7f00])
    const httpBadge = screen.getByText('HTTP');
    expect(httpBadge).toBeTruthy();
    expect(httpBadge.className).toContain('bg-[#fff2e5]');
    expect(httpBadge.className).toContain('text-[#ff7f00]');
  });

  it('shows correct status badges for active and draft', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/data-sources')) return jsonResponse(mockDataSources);
      return jsonResponse(mockTemplates);
    });
    vi.stubGlobal('fetch', fetchMock);

    renderComponent();

    await screen.findByText('用户订单查询');

    // active 状态 - 已启用 (绿色 bg-[#e9f7ef] text-[#2cb360])
    const activeBadge = screen.getByText('已启用');
    expect(activeBadge).toBeTruthy();
    expect(activeBadge.className).toContain('bg-[#e9f7ef]');
    expect(activeBadge.className).toContain('text-[#2cb360]');

    // draft 状态 - 草稿 (灰色 bg-[#f2f3f7] text-[#858b9c])
    const draftBadge = screen.getByText('草稿');
    expect(draftBadge).toBeTruthy();
    expect(draftBadge.className).toContain('bg-[#f2f3f7]');
    expect(draftBadge.className).toContain('text-[#858b9c]');
  });

  it('renders data source filter dropdown', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/data-sources')) return jsonResponse(mockDataSources);
      return jsonResponse(mockTemplates);
    });
    vi.stubGlobal('fetch', fetchMock);

    renderComponent();

    await screen.findByText('用户订单查询');

    // 数据源筛选器存在 - Select 触发器
    const filterTrigger = screen.getByRole('combobox');
    expect(filterTrigger).toBeTruthy();
    // 默认值为 "全部数据源"
    expect(filterTrigger.textContent).toContain('全部数据源');
  });

  it('shows data source name mapping correctly', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/data-sources')) return jsonResponse(mockDataSources);
      return jsonResponse(mockTemplates);
    });
    vi.stubGlobal('fetch', fetchMock);

    renderComponent();

    await screen.findByText('用户订单查询');

    // 数据源名称应该显示名称而不是 ID
    expect(screen.getByText('生产数据库')).toBeTruthy();
    expect(screen.getByText('外部 API')).toBeTruthy();
  });

  it('shows cache_ttl column values', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/data-sources')) return jsonResponse(mockDataSources);
      return jsonResponse(mockTemplates);
    });
    vi.stubGlobal('fetch', fetchMock);

    renderComponent();

    await screen.findByText('用户订单查询');

    // 缓存 TTL 值
    expect(screen.getByText('300')).toBeTruthy();
    expect(screen.getByText('60')).toBeTruthy();
  });

  it('renders create template button', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/data-sources')) return jsonResponse(mockDataSources);
      return jsonResponse([]);
    });
    vi.stubGlobal('fetch', fetchMock);

    renderComponent();

    await screen.findByText('共 0 个');
    expect(screen.getByRole('button', { name: /新建模板/ })).toBeTruthy();
  });
});
