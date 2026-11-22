/**
 * @fileoverview Feature-scoped state of the trading feature: the ticket
 * controller, instrument metadata, order/position caches for chart layers,
 * the chart context menu and pending amendments. Lives for the feature's
 * activation so switching panel tabs never loses a draft.
 */
import {
  TicketController,
  buildContextMenu,
  defaultTicketInputs,
  type ContextMenuItem,
  type InstrumentPrecision,
} from '@joseph/chart-trading';
import {
  Decimal,
  parseInstrumentId,
  type FeedState,
  type Order,
  type Position,
} from '@joseph/contracts';
import {
  createStore,
  type Disposer,
  type WritableStore,
} from '@joseph/feature-registry';
import type {WebFeatureContext} from '../../shell/react-bindings';
import {errorMessage} from './ui/hooks';

/** Market metadata for the ticket's instrument. */
export interface MarketMeta {
  /** Instrument id the metadata belongs to. */
  instrumentId: string;
  /** Precision, or null while loading or unavailable. */
  precision: InstrumentPrecision | null;
  /** Reference mark price. */
  mark: Decimal | null;
  /** Provenance of the mark. */
  feedState: FeedState | null;
  /** Load error, if any. */
  error: string | null;
}

/** Open chart context menu. */
export interface ContextMenuState {
  /** Chart that was right-clicked. */
  chartId: string;
  /** Instrument on that chart. */
  instrumentId: string;
  /** Menu items. */
  items: ContextMenuItem[];
}

/** Amendment proposed by dragging an order line. */
export interface AmendDraft {
  /** Order to amend. */
  order: Order;
  /** Field being changed. */
  field: 'limit_price' | 'trigger_price';
  /** Current price. */
  from: string;
  /** Proposed tick-rounded price. */
  price: string;
}

/** Runtime state of the trading feature. */
export interface TradingRuntime {
  /** Ticket controller. */
  controller: TicketController;
  /** Metadata of the ticket instrument. */
  meta: WritableStore<MarketMeta>;
  /** Orders of the account (all instruments). */
  orders: WritableStore<readonly Order[]>;
  /** Positions of the account. */
  positions: WritableStore<readonly Position[]>;
  /** Context menu state. */
  menu: WritableStore<ContextMenuState | null>;
  /** Pending amendment. */
  amend: WritableStore<AmendDraft | null>;
  /**
   * Reloads orders and positions.
   * @return Promise resolving when done.
   */
  refreshOrders(): Promise<void>;
  /**
   * Loads metadata for an instrument.
   * @param instrumentId Instrument id.
   * @return Promise resolving when done.
   */
  loadMeta(instrumentId: string): Promise<void>;
  /**
   * Returns cached precision for an instrument.
   * @param instrumentId Instrument id.
   * @return Precision or null.
   */
  precisionFor(instrumentId: string): InstrumentPrecision | null;
  /**
   * Opens the context menu for a chart price.
   * @param chartId Chart id.
   * @param instrumentId Instrument id.
   * @param price Display price.
   * @return Promise resolving when the menu is ready.
   */
  openMenu(chartId: string, instrumentId: string, price: number): Promise<void>;
  /** Stops timers and subscriptions. */
  dispose(): void;
}

/**
 * Creates the trading runtime and starts polling.
 * @param ctx Feature context.
 * @param pollMs Order/position refresh interval.
 * @return Runtime.
 */
export function createTradingRuntime(
  ctx: WebFeatureContext,
  pollMs = 3000
): TradingRuntime {
  const selection = ctx.selection.get();
  const controller = new TicketController(
    defaultTicketInputs(selection.instrumentId, selection.accountId),
    {client: ctx.clients.execution}
  );
  const meta = createStore<MarketMeta>({
    instrumentId: selection.instrumentId,
    precision: null,
    mark: null,
    feedState: null,
    error: null,
  });
  const orders = createStore<readonly Order[]>([]);
  const positions = createStore<readonly Position[]>([]);
  const menu = createStore<ContextMenuState | null>(null);
  const amend = createStore<AmendDraft | null>(null);
  const precisionCache = new Map<string, InstrumentPrecision>();
  const disposers: Disposer[] = [];
  let orderSeq = 0;
  let metaSeq = 0;

  const fetchPrecision = async (
    instrumentId: string
  ): Promise<InstrumentPrecision | null> => {
    const cached = precisionCache.get(instrumentId);
    if (cached) return cached;
    const parts = parseInstrumentId(instrumentId);
    const {instruments} = await ctx.clients.market.listInstruments({
      venue: parts.venue,
      q: parts.venueMarketId,
    });
    const found =
      instruments.find(i => i.instrument_id === instrumentId) ?? null;
    if (found) precisionCache.set(instrumentId, found);
    return found;
  };

  const runtime: TradingRuntime = {
    controller,
    meta,
    orders,
    positions,
    menu,
    amend,
    async refreshOrders() {
      const seq = (orderSeq += 1);
      try {
        const [orderList, positionList] = await Promise.all([
          ctx.clients.execution.listOrders(),
          ctx.clients.execution.listPositions(),
        ]);
        // Apply only the newest response so a slow, older read never
        // overwrites fresher state.
        if (seq !== orderSeq) return;
        orders.set(orderList.orders);
        positions.set(positionList.positions);
      } catch {
        // Overlays keep the last known state; the portfolio panel shows errors.
      }
    },
    async loadMeta(instrumentId) {
      const seq = (metaSeq += 1);
      try {
        const [precision, snapshot] = await Promise.all([
          fetchPrecision(instrumentId),
          ctx.clients.market.getSnapshot(instrumentId),
        ]);
        if (seq !== metaSeq) return;
        const markText = snapshot.mark ?? snapshot.last ?? null;
        const mark =
          markText && Decimal.isValid(markText)
            ? Decimal.parse(markText)
            : null;
        if (controller.get().inputs.instrumentId === instrumentId)
          controller.setMarket(precision, mark);
        meta.set({
          instrumentId,
          precision,
          mark,
          feedState: snapshot.stale ? 'STALE' : snapshot.feed_state,
          error: precision
            ? null
            : 'This instrument is not in the registry, so orders cannot be sized.',
        });
      } catch (err) {
        if (seq !== metaSeq) return;
        meta.set({
          instrumentId,
          precision: precisionCache.get(instrumentId) ?? null,
          mark: null,
          feedState: null,
          error: errorMessage(err),
        });
      }
    },
    precisionFor: instrumentId => precisionCache.get(instrumentId) ?? null,
    async openMenu(chartId, instrumentId, price) {
      let precision: InstrumentPrecision | null = null;
      try {
        precision = await fetchPrecision(instrumentId);
      } catch {
        precision = null;
      }
      if (!precision) {
        menu.set({chartId, instrumentId, items: []});
        return;
      }
      const current = meta.get();
      const mark = current.instrumentId === instrumentId ? current.mark : null;
      menu.set({
        chartId,
        instrumentId,
        items: buildContextMenu(price, precision.tick_size, mark),
      });
    },
    dispose() {
      for (const dispose of disposers.splice(0)) dispose();
    },
  };

  // Follow the active chart's instrument and account.
  let lastInstrument = selection.instrumentId;
  disposers.push(
    ctx.selection.subscribe(next => {
      const state = controller.get();
      if (next.instrumentId !== lastInstrument) {
        lastInstrument = next.instrumentId;
        if (state.phase !== 'CONFIRMING' && state.phase !== 'SUBMITTING') {
          controller.dispatch({
            type: 'replaceInputs',
            inputs: {
              ...state.inputs,
              instrumentId: next.instrumentId,
              limitPrice: '',
              triggerPrice: '',
              stopLoss: '',
              takeProfit: '',
            },
          });
          controller.setMarket(
            precisionCache.get(next.instrumentId) ?? null,
            null
          );
          void runtime.loadMeta(next.instrumentId);
        }
      }
      if (
        next.accountId &&
        next.accountId !== state.inputs.accountId &&
        state.phase === 'DRAFT'
      ) {
        controller.edit({accountId: next.accountId});
      }
    })
  );
  disposers.push(
    ctx.clients.userStream.subscribe(
      [{channel: 'orders'}, {channel: 'positions'}, {channel: 'fills'}],
      () => void runtime.refreshOrders()
    )
  );
  const timer = setInterval(() => {
    void runtime.refreshOrders();
    void runtime.loadMeta(controller.get().inputs.instrumentId);
  }, pollMs);
  disposers.push(() => clearInterval(timer));
  void runtime.refreshOrders();
  void runtime.loadMeta(selection.instrumentId);
  return runtime;
}
