/**
 * @fileoverview Builds the backend clients injected into every feature.
 */
import {
  ApiError,
  HttpExecutionClient,
  HttpMarketDataClient,
  HttpStrategyClient,
  WebSocketStreamClient,
  type BackendClients,
  type ExecutionClient,
  type StreamClient,
} from '@joseph/contracts';
import {
  DEMO_INSTRUMENT_SPECS,
  DemoExecutionClient,
  DemoMarketDataClient,
  DemoMarketStream,
  DemoStrategyClient,
  DemoUserStream,
  demoLastPrice,
} from '@joseph/mock-provider';
import {toValidationResult} from '@joseph/strategy-builder';
import type {TerminalConfig} from './env';

/** Clients plus the capability names they provide. */
export interface ClientBundle {
  /** Backend clients. */
  clients: BackendClients;
  /** Capability names (`demo`, `market`, `strategy`, `execution`, ...). */
  capabilities: string[];
}

/**
 * Throws a contract error for every call; used when no execution engine is
 * configured so trading UI fails closed with an actionable message.
 * @return Execution client that always rejects.
 */
export function unavailableExecutionClient(): ExecutionClient {
  const fail = async (): Promise<never> => {
    throw new ApiError(503, {
      code: 'VENUE_UNAVAILABLE',
      message:
        'Trading is not connected. Set NEXT_PUBLIC_EXECUTION_API_URL to an execution engine.',
      trace_id: 'client',
      retryable: false,
      field_errors: [],
    });
  };
  return new Proxy({} as ExecutionClient, {get: () => fail});
}

/** Stream that never emits (no user stream configured). */
const NULL_STREAM: StreamClient = {
  subscribe: () => () => undefined,
  onStatus: handler => {
    handler('CLOSED');
    return () => undefined;
  },
  close: () => undefined,
};

/**
 * Current DEMO mark for broker simulation (same path as DEMO candles; for
 * session markets the last session's close while closed, on the tick
 * ladder).
 * @param instrumentId Instrument.
 * @return Mark price or null for non-DEMO instruments.
 */
export function demoMark(instrumentId: string): string | null {
  const spec = DEMO_INSTRUMENT_SPECS.find(s => s.instrumentId === instrumentId);
  if (!spec) return null;
  return demoLastPrice(spec, Date.now()).price.toString();
}

/** URL query parameter that opts the DEMO into the LIVE arming rehearsal. */
export const DEMO_LIVE_REHEARSAL_PARAM = 'demo-live-rehearsal';

/**
 * Whether the page asked for the DEMO LIVE rehearsal account
 * (`?demo-live-rehearsal=1`). Only DEMO clients read it; a configured
 * execution engine ignores it.
 * @param search Query string; defaults to the current page's.
 * @return True when the rehearsal is requested.
 */
export function demoLiveRehearsalRequested(search?: string): boolean {
  const query =
    search ?? (typeof window === 'undefined' ? '' : window.location.search);
  return new URLSearchParams(query).get(DEMO_LIVE_REHEARSAL_PARAM) === '1';
}

/**
 * Builds DEMO or HTTP clients from configuration.
 * @param config Terminal configuration.
 * @return Clients and capabilities.
 */
export function buildClients(config: TerminalConfig): ClientBundle {
  if (config.demo) {
    const market = new DemoMarketDataClient();
    const execution = new DemoExecutionClient({
      markPrice: demoMark,
      liveRehearsal: demoLiveRehearsalRequested(),
    });
    const strategy = new DemoStrategyClient({
      validate: definition => toValidationResult(definition),
    });
    return {
      clients: {
        market,
        marketStream: new DemoMarketStream(),
        strategy,
        execution,
        userStream: new DemoUserStream(execution, strategy),
        demo: true,
      },
      capabilities: [
        'demo',
        'market',
        'strategy',
        'execution',
        'user-stream',
        'venue:mock',
      ],
    };
  }
  const token = () => config.devToken;
  const marketBase = config.marketApiUrl ?? '';
  const capabilities = ['market', 'strategy'];
  const execution = config.executionApiUrl
    ? new HttpExecutionClient({
        baseUrl: config.executionApiUrl,
        getToken: token,
      })
    : unavailableExecutionClient();
  if (config.executionApiUrl) capabilities.push('execution');
  const userStream = config.executionWsUrl
    ? new WebSocketStreamClient({
        url: () =>
          `${config.executionWsUrl}/v1/stream/user${config.devToken ? `?token=${encodeURIComponent(config.devToken)}` : ''}`,
      })
    : NULL_STREAM;
  if (config.executionWsUrl) capabilities.push('user-stream');
  return {
    clients: {
      market: new HttpMarketDataClient({baseUrl: marketBase, getToken: token}),
      marketStream: new WebSocketStreamClient({
        url: `${config.marketWsUrl ?? ''}/v1/stream/market`,
      }),
      strategy: new HttpStrategyClient({baseUrl: marketBase, getToken: token}),
      execution,
      userStream,
      demo: false,
    },
    capabilities,
  };
}
