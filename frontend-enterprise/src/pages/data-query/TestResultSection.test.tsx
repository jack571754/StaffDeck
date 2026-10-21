// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { I18nProvider } from '@/i18n';
import TestResultSection from './TestResultSection';
import type { QueryExecuteResult } from '@/api/data-query';

function renderComponent(ui: React.ReactElement) {
  return render(<I18nProvider>{ui}</I18nProvider>);
}

const mockResult: QueryExecuteResult = {
  template_id: 'qt_50f463a815af4801',
  columns: ['shop_id', 'shop_name', 'platform', 'net_sales'],
  rows: [
    { shop_id: 'S001', shop_name: '可复美官方旗舰店', platform: '天猫', net_sales: 1250000 },
    { shop_id: 'S002', shop_name: '可丽金官方旗舰店', platform: '抖音', net_sales: 980000 },
    { shop_id: 'S003', shop_name: '巨子生物京东自营店', platform: '京东', net_sales: 620000 },
  ],
  row_count: 3,
  execution_time_ms: 128,
  cached: false,
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('TestResultSection', () => {
  it('renders initial empty state when no result, not loading, and no error', () => {
    renderComponent(<TestResultSection result={null} loading={false} error={null} />);
    expect(screen.getByText('暂无测试运行结果')).toBeTruthy();
    expect(screen.getByText(/填入测试参数后点击「运行测试」/)).toBeTruthy();
  });

  it('renders loading state when loading is true', () => {
    renderComponent(<TestResultSection result={null} loading={true} error={null} />);
    expect(screen.getByText('正在执行查询...')).toBeTruthy();
  });

  it('renders error message when error is provided', () => {
    renderComponent(
      <TestResultSection
        result={null}
        loading={false}
        error="Table 'ds_all.invalid_table' doesn't exist"
      />,
    );
    expect(screen.getByText('查询执行失败')).toBeTruthy();
    expect(screen.getByText(/Table 'ds_all.invalid_table' doesn't exist/)).toBeTruthy();
  });

  it('renders query results with stats, columns, and rows', () => {
    renderComponent(
      <TestResultSection
        result={mockResult}
        loading={false}
        error={null}
        templateName="实时销售汇总播报表"
        dataSourceName="sales-realtime"
      />,
    );

    // 统计徽章
    expect(screen.getByText(/共 3 行 · 4 字段/)).toBeTruthy();
    expect(screen.getByText(/耗时 128 ms/)).toBeTruthy();
    expect(screen.getByText(/源: sales-realtime/)).toBeTruthy();

    // 列名
    expect(screen.getByText('shop_name')).toBeTruthy();
    expect(screen.getByText('net_sales')).toBeTruthy();

    // 数据行
    expect(screen.getByText('可复美官方旗舰店')).toBeTruthy();
    expect(screen.getByText('可丽金官方旗舰店')).toBeTruthy();
    expect(screen.getByText('巨子生物京东自营店')).toBeTruthy();

    // 汇总行 (∑ 及数值列求和 1250000 + 980000 + 620000 = 2,850,000)
    expect(screen.getByText('∑')).toBeTruthy();
    expect(screen.getByText('2,850,000')).toBeTruthy();
  });

  it('updates summary row dynamically when filtered', async () => {
    const user = userEvent.setup();
    renderComponent(<TestResultSection result={mockResult} loading={false} error={null} />);

    // 初始合计 2,850,000
    expect(screen.getByText('2,850,000')).toBeTruthy();

    const searchInput = screen.getByPlaceholderText('搜索结果数据...');
    await user.type(searchInput, '可丽金');

    // 过滤后仅保留可丽金 980,000
    expect(screen.getByText('980,000')).toBeTruthy();
    expect(screen.queryByText('2,850,000')).toBeNull();
  });

  it('filters rows by search term', async () => {
    const user = userEvent.setup();
    renderComponent(<TestResultSection result={mockResult} loading={false} error={null} />);

    const searchInput = screen.getByPlaceholderText('搜索结果数据...');
    await user.type(searchInput, '可丽金');

    expect(screen.getByText('可丽金官方旗舰店')).toBeTruthy();
    expect(screen.queryByText('可复美官方旗舰店')).toBeNull();
    expect(screen.queryByText('巨子生物京东自营店')).toBeNull();
  });

  it('supports exporting to CSV and JSON', async () => {
    const user = userEvent.setup();
    const createObjectURL = vi.fn(() => 'blob:mock');
    const revokeObjectURL = vi.fn();
    window.URL.createObjectURL = createObjectURL;
    window.URL.revokeObjectURL = revokeObjectURL;

    renderComponent(
      <TestResultSection
        result={mockResult}
        loading={false}
        error={null}
        templateName="销售测试"
      />,
    );

    const csvBtn = screen.getByRole('button', { name: /导出 CSV/ });
    const jsonBtn = screen.getByRole('button', { name: /导出 JSON/ });

    expect(csvBtn).toBeTruthy();
    expect(jsonBtn).toBeTruthy();

    await user.click(csvBtn);
    expect(createObjectURL).toHaveBeenCalled();

    await user.click(jsonBtn);
    expect(createObjectURL).toHaveBeenCalledTimes(2);
  });
});
