/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview State of the Python strategy panel: the selected Strategy
 * script and its inputs, backtests, activations, signals and source.
 */

import type {
  ScriptResult,
  Signal,
  StrategyActivation,
  StrategyActivationInput,
  StrategyScript,
  Timeframe,
} from '@joseph/contracts';
import {createStore, type WritableStore} from '@joseph/feature-registry';
import type {WebFeatureContext} from '../../shell/react-bindings';
import {errorMessage} from '../trading/ui/hooks';

/** Request in flight. */
export type StrategyBusy =
  'load' | 'backtest' | 'activate' | 'activation' | 'source' | 'copy';

/** Modes an activation can request (AUTO_LIVE is refused by the engine). */
export type ActivationMode = NonNullable<StrategyActivationInput['mode']>;

/** Panel state. */
export interface StrategyState {
  /** Built-in, plugin and owned strategies. */
  scripts: StrategyScript[];
  /** Whether owned source can execute on the server. */
  runtimeEnabled: boolean;
  /** Selected strategy id, or null before loading. */
  selectedId: string | null;
  /** Input values, starting from the selected strategy's defaults. */
  inputs: Record<string, unknown>;
  /** Closed bars per backtest run and activation pass. */
  bars: number;
  /** Last backtest of the selected strategy, or null. */
  backtest: ScriptResult | null;
  /** The tenant's activations, newest first. */
  activations: StrategyActivation[];
  /** Whether PAPER/SHADOW entries reach the execution engine. */
  forwardingEnabled: boolean;
  /** Recent signals of the selected strategy. */
  signals: Signal[];
  /** Source of the selected strategy once loaded, or null. */
  source: string | null;
  /** Request in flight, or null. */
  busy: StrategyBusy | null;
  /** Last status message, or null. */
  message: {tone: 'info' | 'warning' | 'danger'; text: string} | null;
}

/** Store and actions of the panel. */
export interface StrategyController {
  /** State store. */
  state: WritableStore<StrategyState>;
  /**
   * Loads strategies, activations and signals.
   * @return Promise that never rejects; errors become `message`.
   */
  load(): Promise<void>;
  /**
   * Selects a strategy and resets the inputs to its defaults.
   * @param scriptId Strategy id.
   */
  select(scriptId: string): void;
  /**
   * Sets one input value.
   * @param name Input name.
   * @param value JSON value; undefined restores the declared default.
   */
  setInput(name: string, value: unknown): void;
  /** Restores the selected strategy's default inputs. */
  resetInputs(): void;
  /**
   * Sets the bars per run.
   * @param bars Closed bars, 50 to 2000.
   */
  setBars(bars: number): void;
  /**
   * Backtests the selected strategy on closed bars.
   * @param instrumentId Instrument.
   * @param tf Timeframe.
   * @return Promise that never rejects.
   */
  runBacktest(instrumentId: string, tf: Timeframe): Promise<void>;
  /**
   * Activates the selected strategy on future closed bars.
   * @param request Instrument, timeframe, mode and channels.
   * @return Promise that never rejects.
   */
  activate(request: {
    instrumentId: string;
    tf: Timeframe;
    mode: ActivationMode;
    channels: string[];
  }): Promise<void>;
  /**
   * Pauses, resumes or deletes an activation.
   * @param activationId Activation id.
   * @param action What to do.
   * @return Promise that never rejects.
   */
  change(
    activationId: string,
    action: 'pause' | 'resume' | 'delete'
  ): Promise<void>;
  /**
   * Loads the selected strategy's source.
   * @return Promise that never rejects.
   */
  loadSource(): Promise<void>;
  /**
   * Copies the selected strategy into an owned script.
   * @param newId New owned slug.
   * @return Promise that never rejects.
   */
  copy(newId: string): Promise<void>;
}

/**
 * Reads the default input values of a strategy.
 * @param script Strategy, or undefined.
 * @return Defaults by input name.
 */
export function defaultInputs(
  script: StrategyScript | undefined
): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(script?.inputs ?? {}).map(([name, spec]) => [
      name,
      spec?.default ?? null,
    ])
  );
}

/**
 * Creates the panel controller.
 * @param ctx Feature context with the strategy client.
 * @return Controller.
 */
export function createStrategyController(
  ctx: WebFeatureContext
): StrategyController {
  const state = createStore<StrategyState>({
    scripts: [],
    runtimeEnabled: false,
    selectedId: null,
    inputs: {},
    bars: 500,
    backtest: null,
    activations: [],
    forwardingEnabled: false,
    signals: [],
    source: null,
    busy: null,
    message: null,
  });
  const client = ctx.clients.strategy;
  const patch = (next: Partial<StrategyState>) =>
    void state.update(s => ({...s, ...next}));
  const fail = (err: unknown) =>
    void patch({
      busy: null,
      message: {tone: 'danger', text: errorMessage(err)},
    });
  const selected = () => {
    const s = state.get();
    return s.scripts.find(row => row.script_id === s.selectedId);
  };
  const refreshActivity = async () => {
    const id = state.get().selectedId;
    const [list, signals] = await Promise.all([
      client.listActivations(),
      id
        ? client.listSignals({strategyId: id, limit: 20})
        : Promise.resolve({signals: []}),
    ]);
    patch({
      activations: list.activations,
      forwardingEnabled: list.forwarding_enabled,
      signals: signals.signals,
    });
  };
  return {
    state,
    async load() {
      patch({busy: 'load', message: null});
      try {
        const list = await client.listStrategyScripts();
        const current = state.get().selectedId;
        const keep = list.scripts.find(row => row.script_id === current);
        const first = keep ?? list.scripts[0];
        patch({
          scripts: list.scripts,
          runtimeEnabled: list.runtime_enabled,
          selectedId: first?.script_id ?? null,
          inputs: keep ? state.get().inputs : defaultInputs(first),
        });
        await refreshActivity();
        patch({busy: null});
      } catch (err) {
        fail(err);
      }
    },
    select(scriptId) {
      const script = state.get().scripts.find(r => r.script_id === scriptId);
      patch({
        selectedId: scriptId,
        inputs: defaultInputs(script),
        backtest: null,
        source: null,
        message: null,
      });
      void refreshActivity().catch(fail);
    },
    setInput(name, value) {
      const inputs = {...state.get().inputs};
      // A cleared field falls back to the declared default on the server.
      if (value === undefined) delete inputs[name];
      else inputs[name] = value;
      patch({inputs});
    },
    resetInputs() {
      patch({inputs: defaultInputs(selected())});
    },
    setBars(bars) {
      patch({bars: Math.min(2000, Math.max(50, Math.round(bars) || 500))});
    },
    async runBacktest(instrumentId, tf) {
      const script = selected();
      if (!script) return;
      patch({busy: 'backtest', message: null});
      try {
        const result = await client.backtestStrategy(script.script_id, {
          version: script.version || undefined,
          instrument_id: instrumentId,
          tf,
          limit: state.get().bars,
          inputs: state.get().inputs,
        });
        patch({backtest: result, busy: null});
      } catch (err) {
        fail(err);
      }
    },
    async activate({instrumentId, tf, mode, channels}) {
      const script = selected();
      if (!script) return;
      patch({busy: 'activate', message: null});
      try {
        const created = await client.createActivation({
          script_id: script.script_id,
          version: script.version || undefined,
          instrument_ids: [instrumentId],
          tf,
          inputs: state.get().inputs,
          mode,
          channels: channels.slice(0, 8) as StrategyActivationInput['channels'],
          history_bars: state.get().bars,
        });
        await refreshActivity();
        patch({
          busy: null,
          message: {
            tone: 'info',
            text: `Activated ${created.name} in ${mode.replace('_', ' ').toLowerCase()} mode from the next closed bar. Deployments bind ${created.strategy_version_id}.`,
          },
        });
      } catch (err) {
        fail(err);
      }
    },
    async change(activationId, action) {
      patch({busy: 'activation', message: null});
      try {
        if (action === 'pause') await client.pauseActivation(activationId);
        else if (action === 'resume') {
          await client.resumeActivation(activationId);
        } else await client.deleteActivation(activationId);
        await refreshActivity();
        patch({busy: null});
      } catch (err) {
        fail(err);
      }
    },
    async loadSource() {
      const script = selected();
      if (!script) return;
      patch({busy: 'source', message: null});
      try {
        patch({
          source: await client.getStrategySource(script.script_id),
          busy: null,
        });
      } catch (err) {
        fail(err);
      }
    },
    async copy(newId) {
      const script = selected();
      if (!script) return;
      patch({busy: 'copy', message: null});
      try {
        const saved = await client.copyStrategy(script.script_id, newId);
        const list = await client.listStrategyScripts();
        patch({
          scripts: list.scripts,
          busy: null,
          message: {
            tone: 'info',
            text: `Copied to your script ${saved.script_id} (version ${saved.version}). Edit it in the Python editor.`,
          },
        });
      } catch (err) {
        fail(err);
      }
    },
  };
}
