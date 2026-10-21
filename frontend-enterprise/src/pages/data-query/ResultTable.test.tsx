// @vitest-environment jsdom

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import ResultTable, {
  computeSummaryRow,
  exportToCsv,
  exportToJson,
  isNonSummableColumn,
} from './ResultTable';

describe('ResultTable and Export functions', () => {
  it('renders columns and rows properly with summary row', () => {
    const columns = ['name', 'amount', 'ratio'];
    const rows = [
      { name: '旗舰店', amount: 1000, ratio: '25%' },
      { name: '自营店', amount: 2000, ratio: '50%' },
    ];

    render(<ResultTable columns={columns} rows={rows} />);

    expect(screen.getByText('name')).toBeTruthy();
    expect(screen.getByText('amount')).toBeTruthy();
    expect(screen.getByText('ratio')).toBeTruthy();
    expect(screen.getByText('旗舰店')).toBeTruthy();
    expect(screen.getByText('1000')).toBeTruthy();
    expect(screen.getByText('自营店')).toBeTruthy();

    // 验证汇总行
    expect(screen.getByText(/合计 \(2行\)/)).toBeTruthy();
    expect(screen.getByText('3,000')).toBeTruthy();
  });

  it('correctly identifies non-summable columns vs summable columns', () => {
    expect(isNonSummableColumn('author_id')).toBe(true);
    expect(isNonSummableColumn('商品id')).toBe(true);
    expect(isNonSummableColumn('pay_date')).toBe(true);
    expect(isNonSummableColumn('支付日期')).toBe(true);
    expect(isNonSummableColumn('退款率')).toBe(true);
    expect(isNonSummableColumn('rk')).toBe(true);

    expect(isNonSummableColumn('成交金额')).toBe(false);
    expect(isNonSummableColumn('退款金额')).toBe(false);
    expect(isNonSummableColumn('净销')).toBe(false);
    expect(isNonSummableColumn('net_sales')).toBe(false);
    expect(isNonSummableColumn('gmv')).toBe(false);
  });

  it('computes summary row accurately for complex columns', () => {
    const columns = ['平台', '达人id', '日期', '成交金额', '退款金额', '净销'];
    const rows = [
      { 平台: '抖音', 达人id: '101', 日期: '2026-10-20', 成交金额: 100.5, 退款金额: 10.0, 净销: 90.5 },
      { 平台: '天猫', 达人id: '102', 日期: '2026-10-20', 成交金额: 200.0, 退款金额: 20.0, 净销: 180.0 },
    ];

    const summary = computeSummaryRow(columns, rows);
    expect(summary['平台']).toBe('合计 (2行)');
    expect(summary['达人id']).toBe('—');
    expect(summary['日期']).toBe('—');
    expect(summary['成交金额']).toBe(300.5);
    expect(summary['退款金额']).toBe(30);
    expect(summary['净销']).toBe(270.5);
  });

  it('renders empty message when no rows are passed', () => {
    render(<ResultTable columns={['id']} rows={[]} />);
    expect(screen.getByText('暂无数据')).toBeTruthy();
  });

  it('exportToCsv creates a Blob with BOM and summary row and triggers download', () => {
    let capturedBlob: Blob | null = null;
    window.URL.createObjectURL = vi.fn((blob: Blob) => {
      capturedBlob = blob;
      return 'blob:mock-csv';
    });
    window.URL.revokeObjectURL = vi.fn();

    const columns = ['shop', 'sales'];
    const rows = [{ shop: '天猫店', sales: 888 }];

    exportToCsv(columns, rows, 'test_export');

    expect(window.URL.createObjectURL).toHaveBeenCalled();
    expect(capturedBlob).not.toBeNull();
  });

  it('exportToJson creates a Blob with json format and triggers download', () => {
    let capturedBlob: Blob | null = null;
    window.URL.createObjectURL = vi.fn((blob: Blob) => {
      capturedBlob = blob;
      return 'blob:mock-json';
    });
    window.URL.revokeObjectURL = vi.fn();

    const rows = [{ shop: '天猫店', sales: 888 }];
    exportToJson(rows, 'test_export_json');

    expect(window.URL.createObjectURL).toHaveBeenCalled();
    expect(capturedBlob).not.toBeNull();
  });

  it('renders export buttons in ResultTable when showExport is true', async () => {
    const user = userEvent.setup();
    window.URL.createObjectURL = vi.fn(() => 'blob:mock');
    window.URL.revokeObjectURL = vi.fn();

    render(
      <ResultTable
        columns={['col1']}
        rows={[{ col1: 'val1' }]}
        showExport={true}
        exportFilename="custom_name"
      />,
    );

    const csvButton = screen.getByRole('button', { name: /导出 CSV/ });
    const jsonButton = screen.getByRole('button', { name: /导出 JSON/ });
    expect(csvButton).toBeTruthy();
    expect(jsonButton).toBeTruthy();

    await user.click(csvButton);
    expect(window.URL.createObjectURL).toHaveBeenCalled();
  });
});
