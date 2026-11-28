/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Session markets in the shell: market groups and search
 * ranking (including Chinese names), the picker's tabs, badges and data
 * access flags, ladder-precision prices, yields in %, share and lot
 * volumes, exchange-time bar labels, the market status badge and the
 * classified delta source in the data window.
 */

import type {ChartBar} from '@joseph/chart-core';
import {
  createBarCalendar,
  dayNumberOf,
  marketStatus,
  zonedTimeToUtc,
  type Instrument,
  type VenueCapabilities,
  type VenueStatusList,
} from '@joseph/contracts';
import {
  DemoMarketDataClient,
  demoInstrument,
  DEMO_INSTRUMENT_SPECS,
} from '@joseph/mock-provider';
import {activeChart, createChartState} from '@joseph/terminal-shell';
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import {describe, expect, it, vi} from 'vitest';
import {composeFeatures} from '../composition/features';
import {formatGateValue} from '../features/screener/screener-panel';
import {
  formatMoneyCompact,
  tickDescription,
} from '../features/market-info/market-info-feature';
import {createRuntime, type TerminalRuntime} from '../composition/runtime';
import {DataWindow, studyDataRows} from './data-window';
import {
  displayTickSize,
  formatBarClose,
  formatBarTime,
  formatInstrumentPrice,
  formatVolume,
  tickAt,
} from './format';
import {rankInstruments} from './instrument-selector';
import {describeMarketStatus, MarketStatusBadge} from './market-status';
import {
  delayLabel,
  isRateInstrument,
  isSecurity,
  GROUP_VENUES,
  marketGroupOf,
  marketTypeLabel,
  searchRank,
  venueAccess,
} from './markets';
import {RuntimeProvider} from './runtime-context';
import {TopBar} from './top-bar';

/** DEMO configuration. */
const DEMO = {
  marketApiUrl: null,
  marketWsUrl: null,
  executionApiUrl: null,
  executionWsUrl: null,
  devToken: null,
  demo: true,
};

const NOW = Date.UTC(2026, 10, 23, 15);
const INSTRUMENTS = DEMO_INSTRUMENT_SPECS.map(spec =>
  demoInstrument(spec, NOW)
);

/**
 * Returns the DEMO instrument with an id.
 * @param id Instrument id.
 * @return Instrument.
 * @throws `Error` when no DEMO instrument has the id.
 */
function demo(id: string): Instrument {
  const found = INSTRUMENTS.find(i => i.instrument_id === id);
  if (!found) throw new Error(`missing ${id}`);
  return found;
}

/**
 * Converts a New York wall time to an instant.
 * @param date Day as `YYYY-MM-DD`.
 * @param hour Hour of the day.
 * @param minute Minute of the hour.
 * @return Epoch ms.
 */
function ny(date: string, hour = 0, minute = 0): number {
  return zonedTimeToUtc(
    dayNumberOf(date),
    hour * 60 + minute,
    'America/New_York'
  );
}

/**
 * Creates and activates a DEMO runtime.
 * @return Runtime.
 */
async function demoRuntime(): Promise<TerminalRuntime> {
  const runtime = createRuntime(composeFeatures, DEMO);
  await runtime.registry.activateAll();
  return runtime;
}

describe('market groups and search', () => {
  it('groups by asset class and calendar', () => {
    expect(marketGroupOf(demo('mock:perp:BTCUSD'))).toBe('crypto');
    expect(marketGroupOf(demo('mock:stock:ACME'))).toBe('us');
    expect(marketGroupOf(demo('mock:stock:9901'))).toBe('tw');
    expect(marketGroupOf(demo('mock:etf:TLTD'))).toBe('bonds');
    expect(marketGroupOf(demo('mock:rate:UST10Y'))).toBe('bonds');
    expect(
      marketGroupOf({
        instrument_id: 'twse:stock:2330',
        asset_class: 'EQUITY',
      })
    ).toBe('tw');
    // TPEx stocks are Taiwan stocks; TPEx bond ETFs are bonds.
    expect(
      marketGroupOf({instrument_id: 'tpex:stock:6488', asset_class: 'EQUITY'})
    ).toBe('tw');
    expect(
      marketGroupOf({instrument_id: 'tpex:etf:00679B', asset_class: 'BOND'})
    ).toBe('bonds');
    expect(marketGroupOf(demo('mock:etf:00999B'))).toBe('bonds');
    // Cboe BZX listings are US stocks (XNYS); its bond ETFs are bonds.
    expect(
      marketGroupOf({instrument_id: 'cboe:etf:AAAU', asset_class: 'EQUITY'})
    ).toBe('us');
    expect(
      marketGroupOf({instrument_id: 'cboe:etf:GOVT', asset_class: 'BOND'})
    ).toBe('bonds');
    expect(GROUP_VENUES.tw).toEqual(['twse', 'tpex']);
    expect(GROUP_VENUES.us).toEqual(['nasdaq', 'nyse', 'cboe']);
    expect(GROUP_VENUES.bonds).toContain('tpex');
    expect(GROUP_VENUES.bonds).toContain('cboe');
    expect(isRateInstrument('mock:rate:UST10Y')).toBe(true);
    expect(isSecurity('mock:etf:TLTD')).toBe(true);
    expect(isSecurity('bad id')).toBe(false);
    expect(marketTypeLabel('rate')).toBe('Yield');
  });

  it('ranks symbols before names and finds Chinese names', () => {
    const tw = demo('mock:stock:9901');
    expect(searchRank(tw, '9901')).toBe(0);
    expect(searchRank(tw, '99')).toBe(1);
    expect(searchRank(tw, '示範')).toBe(4);
    expect(searchRank(tw, '半導體')).toBe(5);
    expect(searchRank(tw, 'zzz')).toBeNull();
    expect(searchRank(demo('mock:stock:ACME'), 'acme corp')).toBe(4);
    const ranked = rankInstruments([], INSTRUMENTS, '半導', 'all');
    expect(ranked.map(i => i.instrument_id)).toEqual(['mock:stock:9901']);
    expect(
      rankInstruments(INSTRUMENTS, [], '', 'bonds')
        .map(i => i.instrument_id)
        .sort()
    ).toEqual(['mock:etf:00999B', 'mock:etf:TLTD', 'mock:rate:UST10Y']);
    expect(
      rankInstruments([], INSTRUMENTS, '美債', 'all').map(i => i.instrument_id)
    ).toEqual(['mock:etf:00999B']);
    // Engine matches are kept even when the browser rule would miss them.
    expect(
      rankInstruments([demo('mock:perp:BTCUSD')], [], 'XBT', 'all')
    ).toHaveLength(1);
  });

  it('flags delayed data and a missing Alpaca key', () => {
    // Deliberate partial mock: `venueAccess` reads only these fields.
    const nasdaq = {
      venue: 'nasdaq',
      display_name: 'Nasdaq',
      data_delay_ms: 900_000,
      requires_credentials: true,
      credentials_configured: false,
    } as unknown as VenueCapabilities;
    const access = venueAccess(nasdaq);
    expect(access.delay).toBe('Delayed 15 min');
    expect(access.missingCredentials).toMatch(/^Alpaca API key missing/);
    const cboe = venueAccess({
      ...nasdaq,
      venue: 'cboe',
      display_name: 'Cboe BZX',
    });
    expect(cboe.missingCredentials).toMatch(/^Alpaca API key missing/);
    expect(venueAccess(null)).toEqual({delay: null, missingCredentials: null});
    expect(delayLabel(7_200_000)).toBe('Delayed 2 h');
    expect(delayLabel(5000)).toBe('Delayed 5 s');
  });
});

describe('session formatting', () => {
  it('uses the TWSE ladder and percent for yields', () => {
    const tw = demo('mock:stock:9901');
    expect(tickAt(tw.tick_size, tw.tick_ladder, 585)).toBe('1');
    expect(tickAt(tw.tick_size, tw.tick_ladder, 48.5)).toBe('0.05');
    expect(formatInstrumentPrice('585', tw)).toBe('585');
    expect(formatInstrumentPrice('48.55', tw)).toBe('48.55');
    expect(formatInstrumentPrice('1085', tw)).toBe('1,085');
    expect(displayTickSize(tw, 526, 643)).toBe('1');
    expect(displayTickSize(tw, 450, 550)).toBe('0.5');
    expect(displayTickSize(demo('mock:stock:ACME'), 1, 2)).toBe('0.01');
    expect(formatInstrumentPrice('4.253', demo('mock:rate:UST10Y'))).toBe(
      '4.253%'
    );
    // TPEx ETFs: 0.01 below 50, 0.05 from 50.
    const etf = demo('mock:etf:00999B');
    expect(tickAt(etf.tick_size, etf.tick_ladder, 26.15)).toBe('0.01');
    expect(tickAt(etf.tick_size, etf.tick_ladder, 57.7)).toBe('0.05');
    expect(formatInstrumentPrice('26.15', etf)).toBe('26.15');
    expect(formatInstrumentPrice(null, tw)).toBe('—');
    expect(formatInstrumentPrice('1.5', null)).toBe('1.50');
  });

  it('shows TWSE volume in lots (張) and stock volume in shares', () => {
    expect(formatVolume(1_234_000, demo('mock:stock:9901'))).toBe('1,234 張');
    expect(formatVolume(56_441_000, demo('mock:etf:00999B'))).toBe('56,441 張');
    expect(formatVolume(52_300, demo('mock:stock:ACME'))).toBe('52,300 sh');
    expect(formatVolume(12.5, demo('mock:perp:BTCUSD'))).toBe('12.50');
    expect(formatVolume(null, null)).toBe('—');
  });

  it('labels session bars in exchange time', () => {
    const xnys = createBarCalendar('XNYS');
    const open = ny('2026-11-23', 9, 30);
    expect(formatBarTime('1h', open, xnys)).toBe('2026-11-23 09:30 ET');
    expect(formatBarTime('1d', open, xnys)).toBe('2026-11-23');
    expect(formatBarTime('1M', open, xnys)).toBe('Nov 2026');
    expect(formatBarTime('5s', open, xnys)).toBe('2026-11-23 09:30:00 ET');
    expect(formatBarClose('1h', ny('2026-11-23', 15, 30), xnys)).toBe(
      '16:00:00 ET'
    );
    expect(formatBarClose('1d', open, xnys)).toBe('2026-11-23 16:00 ET');
    // 24x7 keeps UTC.
    expect(formatBarTime('1h', Date.UTC(2026, 10, 23, 14))).toBe(
      '2026-11-23 14:00 UTC'
    );
  });
});

describe('market status badge', () => {
  const xnys = createBarCalendar('XNYS');

  it('describes open, closed, holiday and early-close states', () => {
    const now = ny('2026-11-23', 11);
    const open = describeMarketStatus(
      marketStatus(xnys, now),
      xnys,
      now,
      'NYSE'
    );
    expect(open).toMatchObject({
      state: 'open',
      label: 'Open',
      detail: 'closes 16:00 ET',
      notice: null,
    });
    const evening = ny('2026-11-23', 20);
    expect(
      describeMarketStatus(marketStatus(xnys, evening), xnys, evening, 'NYSE')
        .detail
    ).toBe('opens Tue 09:30 ET');
    const late = ny('2026-11-23', 22);
    expect(
      describeMarketStatus(marketStatus(xnys, late), xnys, late, 'NYSE').detail
    ).toBe('opens 09:30 ET (in 11:30:00)');
    const saturday = ny('2026-11-21', 12);
    expect(
      describeMarketStatus(marketStatus(xnys, saturday), xnys, saturday, 'NYSE')
        .detail
    ).toBe('opens Mon 09:30 ET');
    const holiday = describeMarketStatus(
      {
        ...marketStatus(xnys, saturday),
        holiday: true,
      },
      xnys,
      saturday,
      'NYSE'
    );
    expect(holiday).toMatchObject({
      label: 'Holiday',
      notice: 'NYSE holiday today',
    });
    const early = describeMarketStatus(
      {
        ...marketStatus(xnys, now),
        earlyClose: true,
        session: {
          day: '2026-11-27',
          open_ms: 0,
          close_ms: ny('2026-11-27', 13),
        },
      },
      xnys,
      now,
      'NYSE'
    );
    expect(early.notice).toBe('Early close today at 13:00 ET');
  });

  it('renders for session markets only', () => {
    const {rerender} = render(
      <MarketStatusBadge
        calendar={xnys}
        market="NYSE"
        nowMs={ny('2026-11-23', 11)}
      />
    );
    expect(screen.getByTestId('market-status')).toHaveAttribute(
      'data-state',
      'open'
    );
    rerender(
      <MarketStatusBadge
        calendar={createBarCalendar('24x7')}
        market=""
        nowMs={0}
      />
    );
    expect(screen.queryByTestId('market-status')).toBeNull();
  });
});

describe('data window on session markets', () => {
  it('shows exchange time, lots and study values in lots', () => {
    const bar: ChartBar = {
      timeMs: zonedTimeToUtc(dayNumberOf('2026-11-23'), 9 * 60, 'Asia/Taipei'),
      open: 585,
      high: 590,
      low: 584,
      close: 588,
      volume: 2_500_000,
      final: true,
    };
    render(
      <DataWindow
        bar={bar}
        timeframe="1h"
        calendar={createBarCalendar('XTAI')}
        instrument={demo('mock:stock:9901')}
        previousClose={585}
        tickSize="0.01"
        studyRows={studyDataRows(
          {
            ...createChartState('chart-1', 'mock:stock:9901', '1h'),
            studies: [
              {
                study_id: 'builtin:volume_delta',
                script_id: 'builtin:volume_delta',
                version: 0,
                kind: 'indicator',
                name: 'Delta %',
                visible: true,
                inputs: {},
                colors: {},
              },
            ],
          },
          {
            'chart-1:builtin:volume_delta': {
              script_id: 'builtin:volume_delta',
              version: 0,
              kind: 'indicator',
              source_hash: 'demo',
              instrument_id: 'mock:stock:9901',
              tf: '1h',
              bars_count: 1,
              feed_state: 'DEMO',
              data_source: 'mock',
              complete: true,
              overlay: false,
              plots: [
                {name: 'Delta %', points: [{time_ms: bar.timeMs, value: 8}]},
                {
                  name: 'Buy volume',
                  points: [{time_ms: bar.timeMs, value: 1_350_000}],
                },
              ],
              fills: [],
              equity_curve: [],
              metrics: {},
            },
          },
          bar.timeMs,
          () => 'Delta %',
          null,
          value => formatVolume(value, demo('mock:stock:9901'))
        )}
        compare={[]}
        label={id => id}
      />
    );
    const panel = screen.getByTestId('data-window');
    expect(panel).toHaveTextContent('2026-11-23 09:00 TPE');
    expect(panel).toHaveTextContent('Close588');
    expect(panel).toHaveTextContent('Volume2,500 張');
    expect(panel).toHaveTextContent('Closes10:00:00 TPE');
    expect(panel).toHaveTextContent('Delta %8%');
    expect(panel).toHaveTextContent('Delta % Buy volume1,350 張');
  });
});

describe('instrument picker', () => {
  it('filters by asset-class group and searches Chinese names', async () => {
    const runtime = await demoRuntime();
    render(
      <RuntimeProvider runtime={runtime}>
        <TopBar />
      </RuntimeProvider>
    );
    fireEvent.click(screen.getByTestId('instrument-selector'));
    const listbox = await screen.findByRole('listbox', {name: 'Instruments'});
    await waitFor(
      () =>
        void expect(
          within(listbox).getByRole('group', {name: 'Taiwan stocks'})
        ).toBeVisible()
    );
    for (const group of ['Crypto', 'US stocks', 'Bonds & rates']) {
      expect(within(listbox).getByRole('group', {name: group})).toBeVisible();
    }
    fireEvent.click(screen.getByTestId('market-group-bonds'));
    await waitFor(
      () => void expect(within(listbox).getAllByRole('option')).toHaveLength(3)
    );
    expect(
      within(listbox).getByRole('option', {name: /00999B/})
    ).toHaveTextContent('TPEx');
    expect(
      within(listbox).getByRole('option', {name: /UST10Y/})
    ).toHaveTextContent('Yield, %');
    fireEvent.click(screen.getByTestId('market-group-all'));
    fireEvent.change(screen.getByLabelText('Search instruments'), {
      target: {value: '半導體'},
    });
    const option = await within(listbox).findByRole('option', {name: /9901/});
    expect(option).toHaveTextContent('示範半導體');
    expect(option).toHaveTextContent('DEMO');
    expect(option).toHaveTextContent('TWSE');
    fireEvent.click(option);
    expect(activeChart(runtime.workspace.get()).instrument_id).toBe(
      'mock:stock:9901'
    );
  });

  it('badges delayed venues and venues without an API key', async () => {
    const runtime = await demoRuntime();
    const market = new DemoMarketDataClient();
    const venues = await market.listVenues();
    const [mock] = venues.venues;
    if (!mock) throw new Error('no venue');
    const list: VenueStatusList = {
      ...venues,
      venues: [
        {
          ...mock,
          capabilities: {
            ...mock.capabilities,
            data_delay_ms: 900_000,
            requires_credentials: true,
            credentials_configured: false,
          },
        },
      ],
    };
    vi.spyOn(runtime.clients.market, 'listVenues').mockResolvedValue(list);
    render(
      <RuntimeProvider runtime={runtime}>
        <TopBar />
      </RuntimeProvider>
    );
    fireEvent.click(screen.getByTestId('instrument-selector'));
    const delayed = await screen.findAllByTestId('instrument-delayed');
    expect(delayed[0]).toHaveTextContent('Delayed 15 min');
    expect(
      screen.getAllByTestId('instrument-no-credentials')[0]
    ).toHaveTextContent('No API key');
  });
});

describe('market panel helpers', () => {
  it('formats turnover in the quote currency and describes ladders', () => {
    expect(formatMoneyCompact('2310000000', 'TWD')).toBe('NT$2.31B');
    expect(formatMoneyCompact('106700000', 'USD')).toBe('$106.70M');
    expect(formatMoneyCompact('1200000', 'EUR')).toBe('1.20M EUR');
    expect(formatMoneyCompact(null, 'TWD')).toBe('—');
    expect(tickDescription(demo('mock:stock:9901'))).toBe(
      '0.01 below 10, 0.05 below 50, 0.1 below 100, 0.5 below 500, 1 below 1000, 5 from 1000'
    );
    expect(tickDescription(demo('mock:stock:ACME'))).toBe('0.01');
  });

  it('shows security fields instead of perpetual ones', async () => {
    const runtime = await demoRuntime();
    runtime.dispatch({
      type: 'setInstrument',
      chartId: 'chart-1',
      instrumentId: 'mock:stock:9901',
    });
    const view = runtime.slots
      .observe('side-panel')
      .get()
      .find(c => c.id === 'market-info');
    if (!view) throw new Error('no market panel');
    const View = view.view;
    render(
      <RuntimeProvider runtime={runtime}>
        {/* The market panel uses the context it was activated with. */}
        <View ctx={undefined as never} />
      </RuntimeProvider>
    );
    expect(await screen.findByTestId('market-name')).toHaveTextContent(
      '示範半導體'
    );
    await waitFor(
      () => void expect(screen.getByText('Board lot')).toBeVisible()
    );
    expect(screen.getByText('±10% of the reference price')).toBeVisible();
    expect(screen.getByText(/09:00–13:30 Asia\/Taipei/)).toBeVisible();
    expect(screen.queryByText('Funding (8h)')).toBeNull();
  });
});

describe('screener gates of listed securities', () => {
  it('formats TWD turnover compactly', () => {
    const gate = {
      gate: 'TURNOVER_24H',
      observed: '2310000000',
      threshold: '50000000',
      comparator: 'gt',
      passed: true,
      unit: 'TWD',
      as_of_ms: 0,
      reason: 'Passes',
    } as const;
    expect(formatGateValue(gate, '2310000000')).toBe('NT$2.3B');
    expect(formatGateValue({...gate, unit: 'USD'}, '2310000000')).toBe('$2.3B');
  });
});
