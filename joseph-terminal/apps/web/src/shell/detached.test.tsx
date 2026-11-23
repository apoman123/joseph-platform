/**
 * @fileoverview Main-window UI of detached panes after a reload: the
 * placeholder while pop-outs reconnect, the "Restore pop-outs" banner that
 * waits for the probe, and the per-click browser hint.
 */
import {act, fireEvent, render, screen, within} from '@testing-library/react';
import {describe, expect, it, vi} from 'vitest';
import {createRuntime, type WindowActions} from '../composition/runtime';
import {DetachedBanner, DetachedPlaceholder} from './detached';
import {RuntimeProvider} from './runtime-context';

/** DEMO configuration. */
const DEMO = {
  marketApiUrl: null,
  marketWsUrl: null,
  executionApiUrl: null,
  executionWsUrl: null,
  devToken: null,
  demo: true,
};

/**
 * Fake window actions.
 * @return Actions with spies.
 */
function fakeWindows(): WindowActions {
  return {
    supported: true,
    detach: vi.fn(),
    redock: vi.fn(),
    focus: vi.fn(),
    reopen: vi.fn(),
    reopenAll: vi.fn(),
  };
}

const CHART = {
  key: 'chart:chart-1',
  ref: {kind: 'chart' as const, chartId: 'chart-1'},
  title: 'BTCUSD 5m',
};
const ALERTS = {
  key: 'panel:alerts',
  ref: {
    kind: 'panel' as const,
    slot: 'bottom-panel' as const,
    panelId: 'alerts',
  },
  title: 'Alerts',
};

describe('detached panes after a reload', () => {
  it('shows reconnecting without a re-open button while probing', () => {
    const runtime = createRuntime(() => [], DEMO);
    const windows = fakeWindows();
    runtime.windows.set(windows);
    render(
      <RuntimeProvider runtime={runtime}>
        <DetachedPlaceholder pane={CHART} status="reconnecting" />
      </RuntimeProvider>
    );
    const placeholder = screen.getByTestId(`detached-${CHART.key}`);
    expect(placeholder).toHaveAttribute('data-status', 'reconnecting');
    expect(placeholder).toHaveAttribute('aria-busy', 'true');
    expect(placeholder).toHaveTextContent('Reconnecting to the window');
    expect(within(placeholder).queryByTestId('detached-reopen')).toBeNull();
    expect(within(placeholder).queryByTestId('detached-focus')).toBeNull();
    fireEvent.click(within(placeholder).getByTestId('detached-bring-back'));
    expect(windows.redock).toHaveBeenCalledWith(CHART.key);
  });

  it('offers Restore pop-outs only after the probe', () => {
    const runtime = createRuntime(() => [], DEMO);
    const windows = fakeWindows();
    runtime.windows.set(windows);
    runtime.detached.set({panes: [CHART, ALERTS], live: {}, probing: true});
    render(
      <RuntimeProvider runtime={runtime}>
        <DetachedBanner />
      </RuntimeProvider>
    );
    expect(screen.queryByTestId('detached-banner')).toBeNull();
    act(() => runtime.detached.update(state => ({...state, probing: false})));
    const banner = screen.getByTestId('detached-banner');
    expect(banner).toHaveTextContent(
      '2 panes were open in other windows: BTCUSD 5m, Alerts.'
    );
    expect(banner).toHaveTextContent(/one window per click/);
    const restore = within(banner).getByTestId('reopen-detached');
    expect(restore).toHaveTextContent('Restore pop-outs');
    fireEvent.click(restore);
    expect(windows.reopenAll).toHaveBeenCalledTimes(1);
    fireEvent.click(within(banner).getByText('Keep here'));
    expect(windows.redock).toHaveBeenCalledWith(CHART.key);
    expect(windows.redock).toHaveBeenCalledWith(ALERTS.key);
  });
});
