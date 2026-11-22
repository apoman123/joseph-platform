/**
 * @fileoverview Tests for the workspace reducer, persistence and palette.
 */
import {describe, expect, it, vi} from 'vitest';
import {filterPalette, matchScore, paletteReducer} from './commands';
import {
  WorkspaceRepository,
  createAutosaver,
  decodeShareLink,
  encodeShareLink,
  exportWorkspace,
  parseWorkspace,
} from './persistence';
import {
  activeChart,
  createDefaultWorkspace,
  visibleCharts,
  workspaceReducer,
} from './workspace';

const BASE = createDefaultWorkspace({
  workspaceId: 'ws',
  instrumentId: 'mock:perp:BTCUSD',
  timeframe: '5m',
  nowMs: 1,
});

describe('workspaceReducer', () => {
  it('grows layouts by cloning the active chart and never loses charts when shrinking', () => {
    const four = workspaceReducer(BASE, {type: 'setLayout', layout: '4'}, 2);
    expect(visibleCharts(four).map(c => c.chart_id)).toEqual([
      'chart-1',
      'chart-2',
      'chart-3',
      'chart-4',
    ]);
    const tweaked = workspaceReducer(
      four,
      {type: 'setTimeframe', chartId: 'chart-4', timeframe: '1h'},
      3
    );
    const one = workspaceReducer(tweaked, {type: 'setLayout', layout: '1'}, 4);
    expect(one.charts).toHaveLength(4);
    const back = workspaceReducer(one, {type: 'setLayout', layout: '4'}, 5);
    expect(back.charts[3]?.timeframe).toBe('1h');
    expect(back.updated_at_ms).toBe(5);
  });

  it('syncs instrument changes across linked charts only when enabled', () => {
    let doc = workspaceReducer(BASE, {type: 'setLayout', layout: '2h'}, 2);
    doc = workspaceReducer(
      doc,
      {
        type: 'setInstrument',
        chartId: 'chart-1',
        instrumentId: 'mock:perp:ETHUSD',
      },
      3
    );
    expect(doc.charts.map(c => c.instrument_id)).toEqual([
      'mock:perp:ETHUSD',
      'mock:perp:BTCUSD',
    ]);
    doc = workspaceReducer(
      doc,
      {type: 'setSync', key: 'instrument', value: true},
      4
    );
    doc = workspaceReducer(
      doc,
      {
        type: 'setInstrument',
        chartId: 'chart-2',
        instrumentId: 'mock:perp:SOLUSD',
      },
      5
    );
    expect(doc.charts.map(c => c.instrument_id)).toEqual([
      'mock:perp:SOLUSD',
      'mock:perp:SOLUSD',
    ]);
  });

  it('restricts sync to the link group', () => {
    let doc = workspaceReducer(BASE, {type: 'setLayout', layout: '4'}, 2);
    doc = workspaceReducer(
      doc,
      {type: 'setSync', key: 'timeframe', value: true},
      3
    );
    doc = workspaceReducer(
      doc,
      {type: 'setLinkGroup', chartId: 'chart-1', group: 'A'},
      4
    );
    doc = workspaceReducer(
      doc,
      {type: 'setLinkGroup', chartId: 'chart-3', group: 'A'},
      5
    );
    doc = workspaceReducer(
      doc,
      {type: 'setTimeframe', chartId: 'chart-1', timeframe: '4h'},
      6
    );
    expect(doc.charts.map(c => c.timeframe)).toEqual(['4h', '5m', '4h', '5m']);
  });

  it('toggles favorites, layers and volume', () => {
    let doc = workspaceReducer(
      BASE,
      {type: 'toggleFavoriteTimeframe', timeframe: '5m'},
      2
    );
    expect(doc.favorites?.timeframes).not.toContain('5m');
    doc = workspaceReducer(
      doc,
      {
        type: 'setIndicatorLayer',
        chartId: 'chart-1',
        pluginId: 'premium_discount_ote',
        visible: true,
      },
      3
    );
    expect(
      activeChart(doc).indicator_layers.find(
        l => l.plugin_id === 'premium_discount_ote'
      )?.visible
    ).toBe(true);
    doc = workspaceReducer(doc, {type: 'toggleVolume', chartId: 'chart-1'}, 4);
    expect(activeChart(doc).show_volume).toBe(false);
  });

  it('returns the same object for no-op actions', () => {
    expect(
      workspaceReducer(BASE, {type: 'setActiveChart', chartId: 'missing'}, 9)
    ).toBe(BASE);
    expect(workspaceReducer(BASE, {type: 'rename', name: '   '}, 9)).toBe(BASE);
  });
});

describe('workspace persistence', () => {
  it('imports, exports and migrates the V4 starter format', () => {
    const roundTrip = parseWorkspace(exportWorkspace(BASE), 1);
    expect(roundTrip).toEqual({ok: true, workspace: BASE, migrated: false});
    const legacy = parseWorkspace(
      JSON.stringify({
        exchange: 'binance',
        symbol: 'btcusdt',
        timeframe: '15m',
      }),
      7
    );
    expect(legacy.ok && legacy.workspace.charts[0]?.instrument_id).toBe(
      'binance:perp:BTCUSDT'
    );
    expect(legacy.ok && legacy.migrated).toBe(true);
  });

  it('rejects invalid imports with actionable errors', () => {
    expect(parseWorkspace('{nope', 1)).toEqual({
      ok: false,
      error: 'The file is not valid JSON',
    });
    const bad = {
      ...BASE,
      charts: [{...BASE.charts[0], instrument_id: 'BTCUSDT'}],
    };
    expect(parseWorkspace(JSON.stringify(bad), 1)).toEqual({
      ok: false,
      error: 'Chart chart-1 has an invalid instrument id',
    });
    expect(parseWorkspace('x'.repeat(70_000), 1).ok).toBe(false);
  });

  it('encodes share links without panel state', () => {
    const withPanels = {...BASE, panels: {secret: {note: 'private'}}};
    const decoded = decodeShareLink(encodeShareLink(withPanels), 1);
    expect(decoded.ok && decoded.workspace.panels).toEqual({});
    expect(decodeShareLink('!!!', 1)).toEqual({
      ok: false,
      error: 'The share link is damaged',
    });
  });

  it('autosaves with debounce through the repository', () => {
    vi.useFakeTimers();
    const memory = new Map<string, string>();
    const repo = new WorkspaceRepository({
      getItem: k => memory.get(k) ?? null,
      setItem: (k, v) => void memory.set(k, v),
    });
    const saver = createAutosaver(repo, 100);
    saver.schedule(BASE);
    saver.schedule({...BASE, name: 'Second'});
    expect(memory.size).toBe(0);
    vi.advanceTimersByTime(100);
    expect(repo.load('ws', 1)?.name).toBe('Second');
    saver.schedule({...BASE, name: 'Third'});
    saver.flush();
    expect(repo.load('ws', 1)?.name).toBe('Third');
    vi.useRealTimers();
  });
});

describe('command palette', () => {
  const entries = [
    {id: 'layout.4', title: 'Show 4 charts'},
    {id: 'theme.toggle', title: 'Switch theme'},
    {id: 'drawing.tool.trendline', title: 'Draw trendline'},
  ];

  it('ranks prefix and word matches above subsequences', () => {
    expect(matchScore('Draw trendline', 'tr')).toBeGreaterThan(
      matchScore('Switch theme', 'th') - 1000
    );
    expect(filterPalette(entries, 'trend').map(e => e.id)).toEqual([
      'drawing.tool.trendline',
    ]);
    expect(filterPalette(entries, 'zzz')).toEqual([]);
  });

  it('wraps selection movement', () => {
    const open = paletteReducer(
      {open: false, query: 'x', index: 3},
      {type: 'open'}
    );
    expect(open).toEqual({open: true, query: '', index: 0});
    expect(
      paletteReducer(open, {type: 'move', delta: -1, count: 3}).index
    ).toBe(2);
  });
});
