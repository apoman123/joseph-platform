/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Builds the backend clients injected into every feature.
 */

import {
  ApiError,
  HttpExecutionClient,
  HttpMarketDataClient,
  HttpStrategyClient,
  PythonClient,
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
  DemoScriptsClient,
  DemoStrategyClient,
  DemoStudyClient,
  DemoUserStream,
  demoLastPrice,
} from '@joseph/mock-provider';
import type {TerminalConfig} from './env';
import {historyFirst} from './history-first';

/** Clients plus the capability names they provide. */
export interface ClientBundle {
  /** Backend clients. */
  clients: BackendClients;
  /**
   * Capability names (`demo`, `market`, `strategy`, `execution`,
   * `user-stream`, `venue:mock`) that feature manifests may require.
   */
  capabilities: string[];
}

/**
 * Returns an execution client for deployments without an execution engine,
 * so trading UI fails closed with an actionable message.
 * @return Execution client whose every method rejects with `ApiError` 503
 *     (`VENUE_UNAVAILABLE`).
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
  // Every property read goes through the `get` trap and returns `fail`, so
  // the empty target never needs the client's members.
  const target: Partial<ExecutionClient> = {};
  return new Proxy(target, {get: () => fail}) as ExecutionClient;
}

/** Stream that never emits and reports itself closed (no user stream). */
const NULL_STREAM: StreamClient = {
  subscribe: () => () => undefined,
  onStatus: handler => {
    handler('CLOSED');
    return () => undefined;
  },
  close: () => undefined,
};

/**
 * Returns the current DEMO mark for broker simulation. It follows the same
 * price path as the DEMO candles: for session markets, the last session's
 * close while the market is closed, on the tick ladder.
 * @param instrumentId Canonical instrument id.
 * @return Mark price as a decimal string, or null for non-DEMO instruments.
 */
export function demoMark(instrumentId: string): string | null {
  const spec = DEMO_INSTRUMENT_SPECS.find(s => s.instrumentId === instrumentId);
  if (!spec) return null;
  return demoLastPrice(spec, Date.now()).price.toString();
}

/** URL query parameter that opts the DEMO into the LIVE arming rehearsal. */
export const DEMO_LIVE_REHEARSAL_PARAM = 'demo-live-rehearsal';

/**
 * Returns whether the page asked for the DEMO LIVE rehearsal account
 * (`?demo-live-rehearsal=1`). Only DEMO clients read it; a configured
 * execution engine ignores it.
 * @param search Query string; defaults to the current page's (empty during
 *     server rendering).
 * @return True when the rehearsal is requested.
 */
export function demoLiveRehearsalRequested(search?: string): boolean {
  const query =
    search ?? (typeof window === 'undefined' ? '' : window.location.search);
  return new URLSearchParams(query).get(DEMO_LIVE_REHEARSAL_PARAM) === '1';
}

/**
 * Builds DEMO or HTTP clients from configuration. Study runs yield to
 * pending candle requests (`historyFirst`). Without an execution
 * engine URL, trading calls reject (`unavailableExecutionClient`); without
 * its WebSocket URL, the user stream stays closed.
 * @param config Terminal configuration.
 * @return Clients and the capabilities they provide.
 */
export function buildClients(config: TerminalConfig): ClientBundle {
  if (config.demo) {
    const gate = historyFirst();
    const market = gate.market(new DemoMarketDataClient());
    const execution = new DemoExecutionClient({
      markPrice: demoMark,
      liveRehearsal: demoLiveRehearsalRequested(),
    });
    const strategy = new DemoStrategyClient();
    return {
      clients: {
        market,
        marketStream: new DemoMarketStream(),
        strategy,
        execution,
        userStream: new DemoUserStream(execution, strategy),
        scripts: new DemoScriptsClient({market}),
        studies: gate.studies(new DemoStudyClient(market)),
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
        // Browsers cannot set headers on a WebSocket handshake, so the
        // development token travels in the query string.
        url: () =>
          `${config.executionWsUrl}/v1/stream/user${config.devToken ? `?token=${encodeURIComponent(config.devToken)}` : ''}`,
      })
    : NULL_STREAM;
  if (config.executionWsUrl) capabilities.push('user-stream');
  const gate = historyFirst();
  return {
    clients: {
      market: gate.market(
        new HttpMarketDataClient({baseUrl: marketBase, getToken: token})
      ),
      marketStream: new WebSocketStreamClient({
        url: `${config.marketWsUrl ?? ''}/v1/stream/market`,
      }),
      strategy: new HttpStrategyClient({baseUrl: marketBase, getToken: token}),
      execution,
      userStream,
      scripts: new PythonClient({baseUrl: marketBase, getToken: token}),
      studies: gate.studies(
        new PythonClient({baseUrl: marketBase, getToken: token})
      ),
      demo: false,
    },
    capabilities,
  };
}
