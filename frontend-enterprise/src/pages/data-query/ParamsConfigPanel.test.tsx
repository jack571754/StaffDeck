// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '@/i18n';

import ParamsConfigPanel, { type QueryParam } from './ParamsConfigPanel';

const sampleParams: QueryParam[] = [
  {
    name: 'user_id',
    type: 'string',
    required: true,
    default: '',
    description: '用户唯一标识',
    enum: null,
  },
  {
    name: 'page',
    type: 'int',
    required: false,
    default: 1,
    description: '页码',
    enum: null,
  },
  {
    name: 'status',
    type: 'string',
    required: false,
    default: 'active',
    description: '订单状态',
    enum: ['active', 'cancelled', 'completed'],
  },
];

afterEach(cleanup);

function renderComponent(params: QueryParam[] = [], onChange = vi.fn(), queryContent?: string, queryType = 'sql') {
  return render(
    <I18nProvider>
      <ParamsConfigPanel
        params={params}
        onChange={onChange}
        queryContent={queryContent}
        queryType={queryType}
      />
    </I18nProvider>,
  );
}

// 通过 title 属性找到按钮（文本在 hidden sm:inline span 中，jsdom 下 DOM 存在但不影响查找）
function getAddParamButton(): HTMLElement {
  // 添加参数按钮是 default variant，在提取参数按钮之后
  const buttons = screen.getAllByRole('button');
  const addBtn = buttons.find((b) => {
    const text = b.textContent || '';
    return text.includes('添加参数');
  });
  if (!addBtn) throw new Error('Add param button not found');
  return addBtn;
}

function getExtractParamButton(): HTMLElement {
  // 提取参数按钮有 title="从查询内容中自动提取参数"
  return screen.getByTitle('从查询内容中自动提取参数');
}

describe('ParamsConfigPanel', () => {
  it('renders parameter list with passed props', () => {
    renderComponent(sampleParams);

    expect(screen.getByText('user_id')).toBeTruthy();
    expect(screen.getByText('page')).toBeTruthy();
    expect(screen.getByText('status')).toBeTruthy();
  });

  it('shows parameter count in badge', () => {
    renderComponent(sampleParams);

    // Badge 显示参数数量
    expect(screen.getByText('3')).toBeTruthy();
  });

  it('shows zero count when no params', () => {
    renderComponent([]);

    expect(screen.getByText('0')).toBeTruthy();
    expect(screen.getByText('暂无参数，点击「添加参数」或「提取参数」开始')).toBeTruthy();
  });

  it('shows asterisk for required parameters', () => {
    renderComponent(sampleParams);

    // 必填参数旁边有红色星号 (text-[#d20b0b])
    const asterisks = screen.getAllByText('*').filter((el) =>
      el.className.includes('text-[#d20b0b]'),
    );
    // user_id 是必填的，应该至少有一个
    expect(asterisks.length).toBeGreaterThanOrEqual(1);
  });

  it('shows parameter type badges with correct colors', () => {
    renderComponent(sampleParams);

    // string 类型 - 蓝色 (bg-[#e8f0ff] text-[#1a71ff])
    const stringBadges = screen.getAllByText('string');
    expect(stringBadges.length).toBeGreaterThanOrEqual(1);
    expect(stringBadges[0].className).toContain('bg-[#e8f0ff]');
    expect(stringBadges[0].className).toContain('text-[#1a71ff]');

    // int 类型 - 橙色 (bg-[#fff2e5] text-[#ff7f00])
    const intBadge = screen.getByText('int');
    expect(intBadge).toBeTruthy();
    expect(intBadge.className).toContain('bg-[#fff2e5]');
    expect(intBadge.className).toContain('text-[#ff7f00]');
  });

  it('shows default value for each parameter', () => {
    renderComponent(sampleParams);

    // page 的默认值是 1
    expect(screen.getByText('默认值：1')).toBeTruthy();
    // status 的默认值是 active
    expect(screen.getByText('默认值：active')).toBeTruthy();
    // user_id 无默认值
    expect(screen.getByText('默认值：—')).toBeTruthy();
  });

  it('shows enum values for parameters with enum', () => {
    renderComponent(sampleParams);

    expect(screen.getByText(/枚举：active, cancelled, completed/)).toBeTruthy();
  });

  it('shows parameter description', () => {
    renderComponent(sampleParams);

    expect(screen.getByText('用户唯一标识')).toBeTruthy();
    expect(screen.getByText('页码')).toBeTruthy();
  });

  it('opens add parameter dialog when add button is clicked', async () => {
    const user = userEvent.setup();
    renderComponent([]);

    const addButton = getAddParamButton();
    await user.click(addButton);

    // 对话框应该打开，标题为"添加参数"
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toBeTruthy();
    // 参数名输入框存在
    expect(screen.getByLabelText(/参数名/)).toBeTruthy();
  });

  it('adds a new parameter through the dialog', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderComponent([], onChange);

    // 打开添加对话框
    const addButton = getAddParamButton();
    await user.click(addButton);

    await screen.findByRole('dialog');

    // 输入参数名
    const nameInput = screen.getByLabelText(/参数名/);
    await user.type(nameInput, 'new_param');

    // 点击保存
    const saveButton = screen.getByRole('button', { name: '保存' });
    await user.click(saveButton);

    // 验证 onChange 被调用，新参数被添加
    await waitFor(() => {
      expect(onChange).toHaveBeenCalledTimes(1);
    });
    const newParams = onChange.mock.calls[0][0] as QueryParam[];
    expect(newParams.length).toBe(1);
    expect(newParams[0].name).toBe('new_param');
    expect(newParams[0].type).toBe('string');
    expect(newParams[0].required).toBe(false);
  });

  it('extracts parameters from SQL query content', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const sqlContent = 'SELECT * FROM orders WHERE user_id = :user_id AND status = :status LIMIT :limit';

    renderComponent([], onChange, sqlContent, 'sql');

    // 点击提取参数按钮
    const extractButton = getExtractParamButton();
    expect(extractButton.hasAttribute('disabled')).toBe(false);
    await user.click(extractButton);

    // 验证 onChange 被调用，且提取到了正确的参数
    await waitFor(() => {
      expect(onChange).toHaveBeenCalledTimes(1);
    });
    const extractedParams = onChange.mock.calls[0][0] as QueryParam[];
    expect(extractedParams.length).toBe(3);
    const names = extractedParams.map((p) => p.name);
    expect(names).toContain('user_id');
    expect(names).toContain('status');
    expect(names).toContain('limit');
    // 所有提取的参数默认类型都是 string
    extractedParams.forEach((p) => {
      expect(p.type).toBe('string');
      expect(p.required).toBe(false);
    });
  });

  it('extracts parameters from HTTP query content with path params', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const httpContent = 'GET /api/users/{user_id}/orders?status=:status';

    renderComponent([], onChange, httpContent, 'http');

    const extractButton = getExtractParamButton();
    await user.click(extractButton);

    await waitFor(() => {
      expect(onChange).toHaveBeenCalledTimes(1);
    });
    const extractedParams = onChange.mock.calls[0][0] as QueryParam[];
    expect(extractedParams.length).toBe(2);
    const names = extractedParams.map((p) => p.name);
    expect(names).toContain('user_id');
    expect(names).toContain('status');
  });

  it('skips duplicate parameters when extracting', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const existingParams: QueryParam[] = [
      {
        name: 'user_id',
        type: 'string',
        required: true,
        default: '',
        description: '已存在的参数',
        enum: null,
      },
    ];
    const sqlContent = 'SELECT * FROM orders WHERE user_id = :user_id AND order_id = :order_id';

    renderComponent(existingParams, onChange, sqlContent, 'sql');

    const extractButton = getExtractParamButton();
    await user.click(extractButton);

    await waitFor(() => {
      expect(onChange).toHaveBeenCalledTimes(1);
    });
    const resultParams = onChange.mock.calls[0][0] as QueryParam[];
    // 应该只新增 order_id，user_id 已存在被跳过
    expect(resultParams.length).toBe(2);
    const names = resultParams.map((p) => p.name);
    expect(names).toEqual(expect.arrayContaining(['user_id', 'order_id']));
  });

  it('extract button is disabled when queryContent is empty', () => {
    renderComponent([], vi.fn(), undefined, 'sql');

    const extractButton = getExtractParamButton();
    expect(extractButton.hasAttribute('disabled')).toBe(true);
  });

  it('collapses and expands the panel', async () => {
    const user = userEvent.setup();
    renderComponent(sampleParams);

    // 初始展开，参数可见
    expect(screen.getByText('user_id')).toBeTruthy();

    // 点击标题栏收起（标题是一个 button）
    const headerButton = screen.getByRole('button', { name: /参数配置/ });
    await user.click(headerButton);

    // 收起后参数不可见
    expect(screen.queryByText('user_id')).toBeNull();

    // 再次点击展开
    await user.click(headerButton);
    expect(screen.getByText('user_id')).toBeTruthy();
  });

  it('deletes a parameter when delete button is clicked', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderComponent(sampleParams, onChange);

    // 找到所有删除按钮（title="删除"），点击第一个
    const deleteButtons = screen.getAllByRole('button', { name: '删除' });
    expect(deleteButtons.length).toBe(3);
    await user.click(deleteButtons[0]);

    // 验证 onChange 被调用，删除了第一个参数
    await waitFor(() => {
      expect(onChange).toHaveBeenCalledTimes(1);
    });
    const remainingParams = onChange.mock.calls[0][0] as QueryParam[];
    expect(remainingParams.length).toBe(2);
    expect(remainingParams[0].name).toBe('page');
  });

  it('validates parameter name - empty name shows error', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderComponent([], onChange);

    const addButton = getAddParamButton();
    await user.click(addButton);

    await screen.findByRole('dialog');

    // 不输入参数名直接保存
    const saveButton = screen.getByRole('button', { name: '保存' });
    await user.click(saveButton);

    // onChange 不应该被调用（验证失败）
    expect(onChange).not.toHaveBeenCalled();
  });

  it('validates parameter name - invalid name shows error', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderComponent([], onChange);

    const addButton = getAddParamButton();
    await user.click(addButton);

    await screen.findByRole('dialog');

    // 输入以数字开头的参数名
    const nameInput = screen.getByLabelText(/参数名/);
    await user.type(nameInput, '123_invalid');

    const saveButton = screen.getByRole('button', { name: '保存' });
    await user.click(saveButton);

    // onChange 不应该被调用
    expect(onChange).not.toHaveBeenCalled();
  });

  it('has add parameter button in the header', () => {
    renderComponent([]);

    const addButton = getAddParamButton();
    expect(addButton).toBeTruthy();
  });

  it('shows bool type parameter default correctly', () => {
    const boolParam: QueryParam[] = [
      {
        name: 'is_active',
        type: 'bool',
        required: false,
        default: true,
        description: '是否激活',
        enum: null,
      },
    ];
    renderComponent(boolParam);

    expect(screen.getByText('默认值：true')).toBeTruthy();
    // bool 类型徽章 - 绿色 (bg-[#e9f7ef] text-[#2cb360])
    const boolBadge = screen.getByText('bool');
    expect(boolBadge.className).toContain('bg-[#e9f7ef]');
    expect(boolBadge.className).toContain('text-[#2cb360]');
  });

  it('shows date type parameter badge correctly', () => {
    const dateParam: QueryParam[] = [
      {
        name: 'start_date',
        type: 'date',
        required: false,
        default: '2026-01-01',
        description: '开始日期',
        enum: null,
      },
    ];
    renderComponent(dateParam);

    // date 类型徽章 - 紫色 (bg-[#f3e8ff] text-[#9333ea])
    const dateBadge = screen.getByText('date');
    expect(dateBadge.className).toContain('bg-[#f3e8ff]');
    expect(dateBadge.className).toContain('text-[#9333ea]');
  });

  it('does not extract PostgreSQL ::type casts as parameters from SQL', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    // ::date 和 ::int 是 PostgreSQL 的类型转换，不应该被提取为参数
    const sqlContent = 'SELECT DATE(created_at)::date, id::int FROM orders WHERE user_id = :user_id';

    renderComponent([], onChange, sqlContent, 'sql');

    const extractButton = getExtractParamButton();
    await user.click(extractButton);

    await waitFor(() => {
      expect(onChange).toHaveBeenCalledTimes(1);
    });
    const extractedParams = onChange.mock.calls[0][0] as QueryParam[];
    const names = extractedParams.map((p) => p.name);
    // 只有 :user_id 应该被提取，date 和 int 不应该被提取
    expect(names).toEqual(['user_id']);
    expect(names).not.toContain('date');
    expect(names).not.toContain('int');
  });

  it('saves bool type parameter default as actual boolean value (preserves true)', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    // 预先放入一个 bool 类型、默认值为 true 的参数，打开编辑后直接保存
    // 验证保存后 default 仍然是布尔值 true（而不是被错误地转成 false 或字符串）
    const initialParams: QueryParam[] = [
      {
        name: 'is_active',
        type: 'bool',
        required: false,
        default: true,
        description: '',
        enum: null,
      },
    ];
    renderComponent(initialParams, onChange);

    // 点击编辑按钮
    const editButtons = screen.getAllByRole('button', { name: '编辑' });
    await user.click(editButtons[0]);
    await screen.findByRole('dialog');

    // 不修改任何字段，直接保存
    const saveButton = screen.getByRole('button', { name: '保存' });
    await user.click(saveButton);

    await waitFor(() => {
      expect(onChange).toHaveBeenCalledTimes(1);
    });
    const newParams = onChange.mock.calls[0][0] as QueryParam[];
    expect(newParams.length).toBe(1);
    expect(newParams[0].name).toBe('is_active');
    expect(newParams[0].type).toBe('bool');
    // 关键断言：
    // 1. default 应该是布尔类型（而不是字符串 'true'）
    expect(typeof newParams[0].default).toBe('boolean');
    // 2. 值应该保持为 true，而不是被错误地存为 false
    //    （bug：如果代码用 formData.default === 'true' 比较，bool 的 true 会被转成 false）
    expect(newParams[0].default).toBe(true);
  });

  it('saves bool type parameter default as actual boolean value (preserves false)', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const initialParams: QueryParam[] = [
      {
        name: 'is_disabled',
        type: 'bool',
        required: false,
        default: false,
        description: '',
        enum: null,
      },
    ];
    renderComponent(initialParams, onChange);

    const editButtons = screen.getAllByRole('button', { name: '编辑' });
    await user.click(editButtons[0]);
    await screen.findByRole('dialog');

    const saveButton = screen.getByRole('button', { name: '保存' });
    await user.click(saveButton);

    await waitFor(() => {
      expect(onChange).toHaveBeenCalledTimes(1);
    });
    const newParams = onChange.mock.calls[0][0] as QueryParam[];
    expect(newParams[0].type).toBe('bool');
    expect(typeof newParams[0].default).toBe('boolean');
    expect(newParams[0].default).toBe(false);
  });

  it('handles multiple ::type casts and real params correctly in SQL', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const sqlContent = `
      SELECT
        u.id::int,
        u.name::text,
        u.created_at::date,
        u.amount::numeric(10,2)
      FROM users u
      WHERE u.status = :status
        AND u.created_at > :since
        AND u.department::text = :dept
    `;

    renderComponent([], onChange, sqlContent, 'sql');

    const extractButton = getExtractParamButton();
    await user.click(extractButton);

    await waitFor(() => {
      expect(onChange).toHaveBeenCalledTimes(1);
    });
    const extractedParams = onChange.mock.calls[0][0] as QueryParam[];
    const names = extractedParams.map((p) => p.name);
    // 只有真正的命名参数 :status :since :dept 应该被提取
    expect(names).toEqual(expect.arrayContaining(['status', 'since', 'dept']));
    expect(names).not.toContain('int');
    expect(names).not.toContain('text');
    expect(names).not.toContain('date');
    expect(names).not.toContain('numeric');
    expect(extractedParams.length).toBe(3);
  });
});
