// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { EnterpriseAuthUser } from '@/auth';
import { I18nProvider } from '@/i18n';
import type { ArtifactShareLinkRead } from './ShareLinksPage';
import ShareLinksPage from './ShareLinksPage';

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
  notifyError: vi.fn(),
  notifySuccess: vi.fn(),
  copy: vi.fn(),
}));

vi.mock('@/api/client', () => ({
  api: {
    get: mocks.get,
    post: mocks.post,
    patch: mocks.patch,
    delete: mocks.delete,
  },
}));

vi.mock('@/lib/clipboard', () => ({
  copyTextToClipboard: mocks.copy,
}));

vi.mock('@/components/ui/app-toast', () => ({
  notify: {
    error: mocks.notifyError,
    success: mocks.notifySuccess,
  },
}));

const mockUser: EnterpriseAuthUser = {
  id: 'user_admin',
  tenant_id: 'tenant_demo',
  username: 'admin',
  display_name: 'Administrator',
  role: 'admin',
};

const baseLink: ArtifactShareLinkRead = {
  id: 'link_test_1',
  tenant_id: 'tenant_demo',
  session_id: 'session_1',
  owner_kind: 'harness_frame',
  owner_id: 'task_1',
  path: 'reports/sales.html',
  title: '华东大区销售日报',
  token: 'token_initial_123',
  url: 'http://127.0.0.1:5173/api/chat/artifacts/view/token_initial_123',
  created_by_user_id: 'user_admin',
  created_by_username: 'admin',
  ttl_seconds: 604800,
  expires_at: '2026-11-01T08:00:00',
  is_revoked: false,
  revoked_at: null,
  revoked_by_user_id: null,
  view_count: 5,
  last_viewed_at: null,
  created_at: '2026-10-20T08:00:00',
  updated_at: '2026-10-20T08:00:00',
  status: 'active',
  remaining_seconds: 600000,
  bound_task_id: 'task_sched_1',
  bound_task_title: '定时播报任务',
};

function setupMockList(linkItem = baseLink) {
  mocks.get.mockImplementation((url: string) => {
    if (url.includes('/api/chat/artifacts/share-links/recent-reports')) {
      return Promise.resolve([]);
    }
    if (url.includes('/api/chat/artifacts/share-links')) {
      return Promise.resolve({
        items: [linkItem],
        total: 1,
        active_count: linkItem.is_revoked ? 0 : 1,
        expiring_soon_count: 0,
        expired_count: 0,
        revoked_count: linkItem.is_revoked ? 1 : 0,
        total_views: linkItem.view_count,
      });
    }
    return Promise.resolve([]);
  });
}

function renderPage() {
  return render(
    <I18nProvider>
      <ShareLinksPage currentUser={mockUser} onLogout={vi.fn()} />
    </I18nProvider>
  );
}

beforeEach(() => {
  mocks.get.mockReset();
  mocks.post.mockReset();
  mocks.patch.mockReset();
  mocks.delete.mockReset();
  mocks.notifyError.mockReset();
  mocks.notifySuccess.mockReset();
  mocks.copy.mockReset();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('ShareLinksPage Edit Link Functionality', () => {
  it('modifies title only and sends patch payload without expires_at', async () => {
    setupMockList();
    mocks.patch.mockResolvedValueOnce({
      ...baseLink,
      title: '华东大区销售周报',
    });

    const user = userEvent.setup();
    renderPage();

    // Wait for list to load
    await waitFor(() => {
      expect(screen.getByText('华东大区销售日报')).toBeTruthy();
    });

    // Click "编辑外链信息" button
    const editBtn = screen.getByTitle('编辑外链信息');
    await user.click(editBtn);

    // Dialog should be open
    expect(screen.getByRole('dialog')).toBeTruthy();
    const titleInput = screen.getByLabelText('报告名称') as HTMLInputElement;
    expect(titleInput.value).toBe('华东大区销售日报');

    // Change title
    await user.clear(titleInput);
    await user.type(titleInput, '华东大区销售周报');

    // Save
    const saveBtn = screen.getByRole('button', { name: '保存' });
    await user.click(saveBtn);

    await waitFor(() => {
      expect(mocks.patch).toHaveBeenCalledWith(
        '/api/chat/artifacts/share-links/link_test_1',
        { title: '华东大区销售周报' }
      );
      expect(mocks.notifySuccess).toHaveBeenCalledWith('外链已更新');
    });
  });

  it('modifies expiration time and sends patch payload with ISO expires_at', async () => {
    setupMockList();
    mocks.patch.mockResolvedValueOnce({
      ...baseLink,
      expires_at: '2026-11-15T12:00:00.000Z',
    });

    const user = userEvent.setup();
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('华东大区销售日报')).toBeTruthy();
    });

    const editBtn = screen.getByTitle('编辑外链信息');
    await user.click(editBtn);

    const expiresInput = screen.getByLabelText('到期时间') as HTMLInputElement;
    expect(expiresInput.disabled).toBe(false);

    // Type a future date
    await user.clear(expiresInput);
    await user.type(expiresInput, '2026-11-15T12:00');

    const saveBtn = screen.getByRole('button', { name: '保存' });
    await user.click(saveBtn);

    await waitFor(() => {
      expect(mocks.patch).toHaveBeenCalled();
      const [calledUrl, payload] = mocks.patch.mock.calls[0];
      expect(calledUrl).toBe('/api/chat/artifacts/share-links/link_test_1');
      expect(payload).toHaveProperty('expires_at');
      expect(payload).not.toHaveProperty('title');
    });
  });

  it('disables expires_at input for revoked links and only submits title', async () => {
    const revokedLink: ArtifactShareLinkRead = {
      ...baseLink,
      is_revoked: true,
      status: 'revoked',
      title: '已作废报表',
    };
    setupMockList(revokedLink);
    mocks.patch.mockResolvedValueOnce({
      ...revokedLink,
      title: '已作废报表改名',
    });

    const user = userEvent.setup();
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('已作废报表')).toBeTruthy();
    });

    const editBtn = screen.getByTitle('编辑外链信息');
    await user.click(editBtn);

    const expiresInput = screen.getByLabelText('到期时间') as HTMLInputElement;
    expect(expiresInput.disabled).toBe(true);
    expect(screen.getByText('已作废的链接不能修改有效期，请重新生成链接。')).toBeTruthy();

    const titleInput = screen.getByLabelText('报告名称') as HTMLInputElement;
    await user.clear(titleInput);
    await user.type(titleInput, '已作废报表改名');

    const saveBtn = screen.getByRole('button', { name: '保存' });
    await user.click(saveBtn);

    await waitFor(() => {
      expect(mocks.patch).toHaveBeenCalledWith(
        '/api/chat/artifacts/share-links/link_test_1',
        { title: '已作废报表改名' }
      );
    });
  });

  it('rejects removing demo prefix with inline error and does not call api.patch', async () => {
    const demoLink: ArtifactShareLinkRead = {
      ...baseLink,
      title: '【演示数据】销售报表',
    };
    setupMockList(demoLink);

    const user = userEvent.setup();
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('【演示数据】销售报表')).toBeTruthy();
    });

    const editBtn = screen.getByTitle('编辑外链信息');
    await user.click(editBtn);

    expect(screen.getByText('演示数据链接的名称必须保留【演示数据】前缀。')).toBeTruthy();

    const titleInput = screen.getByLabelText('报告名称') as HTMLInputElement;
    await user.clear(titleInput);
    await user.type(titleInput, '正式销售报表');

    const saveBtn = screen.getByRole('button', { name: '保存' });
    await user.click(saveBtn);

    await waitFor(() => {
      expect(screen.getByText('演示数据链接的名称必须保留【演示数据】前缀')).toBeTruthy();
      expect(mocks.patch).not.toHaveBeenCalled();
    });
  });
});
