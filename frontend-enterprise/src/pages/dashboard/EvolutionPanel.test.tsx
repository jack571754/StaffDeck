// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '@/i18n';
import { TooltipProvider } from '@/components/ui/tooltip';
import { notify } from '@/components/ui';
import EvolutionPanel from './EvolutionPanel';

function jsonResponse(body: unknown, status = 200, statusText = 'OK'): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText,
    text: async () => JSON.stringify(body ?? {}),
  } as Response;
}

beforeEach(() => {
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
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('EvolutionPanel feedback empty-state & scan prevention', () => {
  it('prompts and does not send analyze request when negativeFeedbackCount is 0', async () => {
    const notifyInfoSpy = vi.spyOn(notify, 'info').mockImplementation(() => '');
    const fetchedUrls: string[] = [];

    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      fetchedUrls.push(url);
      return jsonResponse([]);
    }));

    render(
      <I18nProvider>
        <TooltipProvider>
          <EvolutionPanel agentId="agent-zero-feedback" negativeFeedbackCount={0} />
        </TooltipProvider>
      </I18nProvider>,
    );

    // Displays badge indicating 0 down-voted feedback
    expect(await screen.findByText(/0 条点踩反馈/)).toBeTruthy();

    // Displays friendly empty state guide
    expect(
      screen.getByText('当前员工暂无点踩反馈。在「对话」中对不符合预期的回复点踩（👎）后，即可在此扫描生成自进化候选。'),
    ).toBeTruthy();

    // Click scan button
    const scanButton = screen.getByRole('button', { name: '扫描反馈并生成候选' });
    await userEvent.click(scanButton);

    // Verify analyze API was not called
    expect(fetchedUrls.some((url) => url.includes('evolution:analyze'))).toBe(false);

    // Verify friendly info toast was triggered
    expect(notifyInfoSpy).toHaveBeenCalledWith(
      expect.stringContaining('当前员工暂无点踩反馈'),
    );
  });

  it('calls analyze and handles EVOLUTION_FEEDBACK_NOT_FOUND gracefully with info notification', async () => {
    const notifyInfoSpy = vi.spyOn(notify, 'info').mockImplementation(() => '');
    const notifyErrorSpy = vi.spyOn(notify, 'error').mockImplementation(() => '');

    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('evolution:analyze')) {
        return jsonResponse(
          { code: 'EVOLUTION_FEEDBACK_NOT_FOUND', message: '未找到可用于改进的 Skill 或 SOP 反馈' },
          404,
          'Not Found',
        );
      }
      return jsonResponse([]);
    }));

    render(
      <I18nProvider>
        <TooltipProvider>
          <EvolutionPanel agentId="agent-has-count-but-not-evolvable" negativeFeedbackCount={1} />
        </TooltipProvider>
      </I18nProvider>,
    );

    expect(await screen.findByText(/1 条点踩反馈/)).toBeTruthy();

    const scanButton = screen.getByRole('button', { name: '扫描反馈并生成候选' });
    await userEvent.click(scanButton);

    await waitFor(() => {
      expect(notifyInfoSpy).toHaveBeenCalledWith(
        '未找到可用于改进的 Skill 或 SOP 反馈',
      );
    });
    expect(notifyErrorSpy).not.toHaveBeenCalled();
  });
});
