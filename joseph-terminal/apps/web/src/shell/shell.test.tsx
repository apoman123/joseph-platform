/**
 * @fileoverview Component tests for shell pieces (jsdom; the canvas chart
 * itself is covered by Playwright).
 */
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import {describe, expect, it} from 'vitest';
import {composeFeatures} from '../composition/features';
import {createRuntime, type TerminalRuntime} from '../composition/runtime';
import {CommandPalette, StatusBar, Toasts} from './chrome';
import {DrawingToolbar} from './drawing-toolbar';
import {ProvenanceBadge, ProvenanceRibbon, provenanceLabel} from './provenance';
import type {WebFeature} from './react-bindings';
import {RuntimeProvider} from './runtime-context';
import {SlotTabs} from './slot-host';
import {TopBar, worstFeedState} from './top-bar';

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
 * Creates and activates a DEMO runtime.
 * @param extra Extra features.
 * @return Runtime.
 */
async function demoRuntime(extra: WebFeature[] = []): Promise<TerminalRuntime> {
  const runtime = createRuntime(rt => [...composeFeatures(rt), ...extra], DEMO);
  await runtime.registry.activateAll();
  return runtime;
}

describe('provenance', () => {
  it('labels DEMO data unmistakably and explains it', () => {
    render(<ProvenanceBadge state="DEMO" />);
    expect(screen.getByTestId('provenance-badge')).toHaveTextContent(
      'Demo data'
    );
    expect(screen.getByTestId('provenance-badge')).toHaveAttribute(
      'title',
      expect.stringMatching(/Synthetic/)
    );
  });

  it('shows stale age and encodes state on the ribbon', () => {
    expect(provenanceLabel('STALE', 42_000)).toBe('Stale for 42 s');
    const {container} = render(<ProvenanceRibbon state="POLL" />);
    expect(container.firstChild).toHaveClass('ribbon--poll');
  });

  it('summarizes the worst provenance across panes', () => {
    expect(worstFeedState({a: 'LIVE', b: 'STALE'})).toBe('STALE');
    expect(worstFeedState({a: 'LIVE', b: 'DEMO'})).toBe('DEMO');
    expect(worstFeedState({})).toBe('CONNECTING');
  });
});

describe('TopBar', () => {
  it('switches timeframe and layout through the workspace', async () => {
    const runtime = await demoRuntime();
    render(
      <RuntimeProvider runtime={runtime}>
        <TopBar />
      </RuntimeProvider>
    );
    fireEvent.click(
      within(screen.getByRole('navigation', {name: 'Timeframe'})).getByRole(
        'button',
        {name: '1h'}
      )
    );
    expect(runtime.selection.get().timeframe).toBe('1h');
    fireEvent.click(screen.getByTestId('layout-menu'));
    fireEvent.click(screen.getByTestId('layout-4'));
    expect(runtime.workspace.get().layout).toBe('4');
    expect(screen.getByTestId('order-mode')).toHaveTextContent('Paper trading');
  });

  it('toggles indicator layers per chart', async () => {
    const runtime = await demoRuntime();
    render(
      <RuntimeProvider runtime={runtime}>
        <TopBar />
      </RuntimeProvider>
    );
    fireEvent.click(screen.getByTestId('indicators-menu'));
    const checkbox = screen.getByRole('checkbox', {
      name: 'Premium, discount and OTE',
    });
    expect(checkbox).not.toBeChecked();
    fireEvent.click(checkbox);
    const chart = runtime.workspace.get().charts[0];
    expect(
      chart?.indicator_layers.find(l => l.plugin_id === 'premium_discount_ote')
        ?.visible
    ).toBe(true);
  });
});

describe('DrawingToolbar', () => {
  it('lists registered tools and arms one via its command', async () => {
    const runtime = await demoRuntime();
    render(
      <RuntimeProvider runtime={runtime}>
        <DrawingToolbar />
      </RuntimeProvider>
    );
    expect(screen.getAllByRole('button').length).toBeGreaterThan(12);
    fireEvent.click(screen.getByTestId('tool-trendline'));
    await waitFor(() =>
      expect(runtime.drawing.activeTool.get()).toBe('trendline')
    );
    expect(screen.getByTestId('tool-trendline')).toHaveAttribute(
      'aria-pressed',
      'true'
    );
    expect(screen.getByTestId('undo')).toBeDisabled();
  });
});

describe('SlotTabs', () => {
  it('renders contributions with their own context and isolates failures', async () => {
    const broken: WebFeature = {
      manifest: {
        id: 'test.broken',
        version: '1.0.0',
        owner: 'test',
        displayName: 'Broken',
        description: 'Throws on render',
        uiSlots: ['bottom-panel'],
        settingsSchemaVersion: 1,
        contractVersion: 1,
        removal: {notes: 'none'},
      },
      activate(ctx) {
        ctx.slots.contribute('bottom-panel', {
          id: 'broken',
          title: 'Broken panel',
          order: 1,
          view: () => {
            throw new Error('boom');
          },
        });
        return () => undefined;
      },
    };
    const runtime = await demoRuntime([broken]);
    const original = console.error;
    console.error = () => undefined;
    render(
      <RuntimeProvider runtime={runtime}>
        <SlotTabs
          slot="bottom-panel"
          label="Bottom panel"
          orientation="bottom"
        />
      </RuntimeProvider>
    );
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Broken panel stopped working: boom'
    );
    fireEvent.click(screen.getByRole('tab', {name: /Data health/}));
    await screen.findByRole('table', {name: 'Venue data health'});
    console.error = original;
  });
});

describe('chrome', () => {
  it('opens the palette and runs a command with the keyboard', async () => {
    const runtime = await demoRuntime();
    render(
      <RuntimeProvider runtime={runtime}>
        <CommandPalette />
      </RuntimeProvider>
    );
    act(() => {
      void runtime.commands.execute('palette.open');
    });
    const input = await screen.findByRole('textbox', {name: 'Search commands'});
    fireEvent.change(input, {target: {value: 'eight charts'}});
    fireEvent.keyDown(input, {key: 'Enter'});
    await waitFor(() => expect(runtime.workspace.get().layout).toBe('8'));
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('shows demo status, attribution and toasts', async () => {
    const runtime = await demoRuntime();
    render(
      <RuntimeProvider runtime={runtime}>
        <StatusBar />
        <Toasts />
      </RuntimeProvider>
    );
    expect(screen.getByText(/Demo mode: synthetic prices/)).toBeInTheDocument();
    expect(
      screen.getByRole('link', {name: /Lightweight Charts/})
    ).toHaveAttribute('href', expect.stringContaining('tradingview'));
    act(() => runtime.notifier.notify('Saved', 'success'));
    expect(screen.getByText('Saved')).toBeInTheDocument();
  });
});
