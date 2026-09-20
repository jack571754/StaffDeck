// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '@/i18n';
import type { DataSource } from '@/api/data-query';

import DataSourceList from './DataSourceList';

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
      <DataSourceList />
    </I18nProvider>,
  );
}

describe('DataSourceList', () => {
  it('renders loading state initially', () => {
    const fetchMock = vi.fn(() => new Promise<Response>(() => {}));
    vi.stubGlobal('fetch', fetchMock);

    renderComponent();

    // 标题区域渲染
    expect(screen.getByText('数据源列表')).toBeTruthy();
    // 表格存在
    expect(screen.getByLabelText('数据源列表')).toBeTruthy();
  });

  it('renders empty state when no data sources exist', async () => {
    const fetchMock = vi.fn(async () => jsonResponse([]));
    vi.stubGlobal('fetch', fetchMock);

    renderComponent();

    expect(await screen.findByText('暂无数据源，点击「新建数据源」创建一个吧')).toBeTruthy();
    expect(screen.getByText('共 0 个')).toBeTruthy();
  });

  it('renders data source list with mock data', async () => {
    const fetchMock = vi.fn(async () => jsonResponse(mockDataSources));
    vi.stubGlobal('fetch', fetchMock);

    renderComponent();

    // 等待数据加载完成
    expect(await screen.findByText('生产数据库')).toBeTruthy();
    expect(screen.getByText('外部 API')).toBeTruthy();
    expect(screen.getByText('共 2 个')).toBeTruthy();

    // 描述也显示
    expect(screen.getByText('主业务 MySQL 数据库')).toBeTruthy();
    expect(screen.getByText('第三方数据接口')).toBeTruthy();
  });

  it('shows correct type badges for MySQL and HTTP API', async () => {
    const fetchMock = vi.fn(async () => jsonResponse(mockDataSources));
    vi.stubGlobal('fetch', fetchMock);

    renderComponent();

    await screen.findByText('生产数据库');

    // MySQL 类型徽章 - 蓝色调 (bg-[#e8f0ff] text-[#1a71ff])
    const mysqlBadge = screen.getByText('MySQL');
    expect(mysqlBadge).toBeTruthy();
    expect(mysqlBadge.className).toContain('bg-[#e8f0ff]');
    expect(mysqlBadge.className).toContain('text-[#1a71ff]');

    // HTTP API 类型徽章 - 橙色调 (bg-[#fff2e5] text-[#ff7f00])
    const httpBadge = screen.getByText('HTTP API');
    expect(httpBadge).toBeTruthy();
    expect(httpBadge.className).toContain('bg-[#fff2e5]');
    expect(httpBadge.className).toContain('text-[#ff7f00]');
  });

  it('shows correct status badges for active and error', async () => {
    const fetchMock = vi.fn(async () => jsonResponse(mockDataSources));
    vi.stubGlobal('fetch', fetchMock);

    renderComponent();

    await screen.findByText('生产数据库');

    // active 状态 - 正常 (绿色 bg-[#e9f7ef] text-[#2cb360])
    const activeBadge = screen.getByText('正常');
    expect(activeBadge).toBeTruthy();
    expect(activeBadge.className).toContain('bg-[#e9f7ef]');
    expect(activeBadge.className).toContain('text-[#2cb360]');

    // error 状态 - 异常 (红色 bg-[#fce7e7] text-[#d20b0b])
    const errorBadge = screen.getByText('异常');
    expect(errorBadge).toBeTruthy();
    expect(errorBadge.className).toContain('bg-[#fce7e7]');
    expect(errorBadge.className).toContain('text-[#d20b0b]');
  });

  it('calls test connection API when test button is clicked', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === 'POST' && url.includes('/test')) {
        return jsonResponse({ success: true, message: '连接成功' });
      }
      return jsonResponse(mockDataSources);
    });
    vi.stubGlobal('fetch', fetchMock);

    renderComponent();

    await screen.findByText('生产数据库');

    // 点击第一个数据源的测试连接按钮
    const testButtons = screen.getAllByRole('button', { name: /测试连接/ });
    expect(testButtons.length).toBeGreaterThanOrEqual(1);
    await user.click(testButtons[0]);

    // 验证 test API 被调用
    await waitFor(() => {
      const testCall = fetchMock.mock.calls.find(
        ([url, init]) =>
          String(url).includes('/data-sources/ds-1/test') && init?.method === 'POST',
      );
      expect(testCall).toBeTruthy();
    });
  });

  it('shows read_only column correctly', async () => {
    const fetchMock = vi.fn(async () => jsonResponse(mockDataSources));
    vi.stubGlobal('fetch', fetchMock);

    renderComponent();

    await screen.findByText('生产数据库');

    // 第一个数据源 read_only=true 显示"是"
    expect(screen.getByText('是')).toBeTruthy();
    // 第二个数据源 read_only=false 显示"否"
    expect(screen.getByText('否')).toBeTruthy();
  });

  it('renders create button', async () => {
    const fetchMock = vi.fn(async () => jsonResponse([]));
    vi.stubGlobal('fetch', fetchMock);

    renderComponent();

    await screen.findByText('共 0 个');
    expect(screen.getByRole('button', { name: /新建数据源/ })).toBeTruthy();
  });

  it('renders refresh button', async () => {
    const fetchMock = vi.fn(async () => jsonResponse([]));
    vi.stubGlobal('fetch', fetchMock);

    renderComponent();

    await screen.findByText('共 0 个');
    expect(screen.getByRole('button', { name: /刷新/ })).toBeTruthy();
  });
});
