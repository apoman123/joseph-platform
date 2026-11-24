/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Order ticket state machine and controller.
 *
 * Phases: DRAFT → PREVIEWING → PREVIEWED → CONFIRMING → SUBMITTING →
 * SUBMITTED | FAILED. Editing any input invalidates the preview, so what is
 * confirmed is always exactly what was previewed. One idempotency key is
 * minted per confirmed action and reused for retries of that same action, so
 * double clicks and network retries can never create a second order.
 */

import {
  ApiError,
  type Decimal,
  type AccountSummary,
  type ExecutionClient,
  type OrderMode,
  type OrderPreview,
  type SubmitIntentResponse,
} from '@joseph/contracts';
import {accountStatusView} from './equity';
import {
  buildIntentRequest,
  type BuildResult,
  type InstrumentPrecision,
  type TicketContext,
  type TicketInputs,
} from './intent-builder';

/** Ticket phase. */
export type TicketPhase =
  | 'DRAFT'
  | 'PREVIEWING'
  | 'PREVIEWED'
  | 'CONFIRMING'
  | 'SUBMITTING'
  | 'SUBMITTED'
  | 'FAILED';

/** User-facing error attached to a ticket. */
export interface TicketError {
  /** Contract error code or a local code. */
  code: string;
  /** Plain-language message. */
  message: string;
  /** Whether retrying the same confirmed action may succeed. */
  retryable: boolean;
}

/** Immutable ticket state. */
export interface TicketState {
  /** Current phase. */
  phase: TicketPhase;
  /** Current inputs. */
  inputs: TicketInputs;
  /** Increments on every input change. */
  inputRevision: number;
  /** Preview for `previewRevision`, if any. */
  preview: OrderPreview | null;
  /** Input revision the preview belongs to. */
  previewRevision: number | null;
  /** Idempotency key of the confirmed action, if one is in flight. */
  idempotencyKey: string | null;
  /** Last submission result. */
  result: SubmitIntentResponse | null;
  /** Last error. */
  error: TicketError | null;
}

/** Ticket actions. */
export type TicketAction =
  | {type: 'edit'; patch: Partial<TicketInputs>}
  | {type: 'replaceInputs'; inputs: TicketInputs}
  | {type: 'previewRequested'}
  | {type: 'previewSucceeded'; preview: OrderPreview; revision: number}
  | {type: 'previewFailed'; error: TicketError; revision: number}
  | {type: 'confirm'; nowMs: number; idempotencyKey: string}
  | {type: 'submitStarted'}
  | {type: 'submitSucceeded'; response: SubmitIntentResponse}
  | {type: 'submitFailed'; error: TicketError}
  | {type: 'reset'};

/**
 * Creates the initial ticket state.
 * @param inputs Initial inputs.
 * @return Draft state.
 */
export function initialTicketState(inputs: TicketInputs): TicketState {
  return {
    phase: 'DRAFT',
    inputs,
    inputRevision: 0,
    preview: null,
    previewRevision: null,
    idempotencyKey: null,
    result: null,
    error: null,
  };
}

/**
 * Returns whether the ticket is busy with a request.
 * @param state Ticket state.
 * @return True while previewing, confirming or submitting.
 */
export function isBusy(state: TicketState): boolean {
  return (
    state.phase === 'PREVIEWING' ||
    state.phase === 'CONFIRMING' ||
    state.phase === 'SUBMITTING'
  );
}

/**
 * Returns whether the current preview may be confirmed now.
 * @param state Ticket state.
 * @param nowMs Current time (UTC ms).
 * @return True when previewed, allowed, current and unexpired.
 */
export function canConfirm(state: TicketState, nowMs: number): boolean {
  return (
    state.phase === 'PREVIEWED' &&
    state.preview !== null &&
    state.preview.allowed &&
    state.previewRevision === state.inputRevision &&
    nowMs < state.preview.expires_at_ms
  );
}

/**
 * Pure reducer implementing the ticket state machine.
 * @param state Current state.
 * @param action Action.
 * @return Next state (the same object when the action is not allowed).
 */
export function ticketReducer(
  state: TicketState,
  action: TicketAction
): TicketState {
  switch (action.type) {
    case 'edit':
    case 'replaceInputs': {
      if (state.phase === 'CONFIRMING' || state.phase === 'SUBMITTING') {
        return state;
      }
      const inputs =
        action.type === 'edit'
          ? {...state.inputs, ...action.patch}
          : action.inputs;
      return {
        ...state,
        phase: 'DRAFT',
        inputs,
        inputRevision: state.inputRevision + 1,
        preview: null,
        previewRevision: null,
        idempotencyKey: null,
        error: null,
      };
    }
    case 'previewRequested':
      if (isBusy(state)) return state;
      return {
        ...state,
        phase: 'PREVIEWING',
        preview: null,
        previewRevision: state.inputRevision,
        error: null,
      };
    case 'previewSucceeded':
      if (
        state.phase !== 'PREVIEWING' ||
        action.revision !== state.inputRevision
      ) {
        return state;
      }
      return {
        ...state,
        phase: 'PREVIEWED',
        preview: action.preview,
        previewRevision: action.revision,
      };
    case 'previewFailed':
      if (
        state.phase !== 'PREVIEWING' ||
        action.revision !== state.inputRevision
      ) {
        return state;
      }
      return {...state, phase: 'FAILED', error: action.error};
    case 'confirm':
      if (state.phase === 'CONFIRMING' || state.phase === 'SUBMITTING') {
        return state;
      }
      if (state.phase === 'FAILED' && state.idempotencyKey && state.preview) {
        // Retrying the same confirmed action keeps its idempotency key.
        return {...state, phase: 'CONFIRMING', error: null};
      }
      if (
        state.phase === 'PREVIEWED' &&
        state.preview &&
        action.nowMs >= state.preview.expires_at_ms
      ) {
        return {
          ...state,
          phase: 'DRAFT',
          preview: null,
          previewRevision: null,
          error: {
            code: 'PREVIEW_EXPIRED',
            message:
              'The preview expired. Preview the order again before confirming.',
            retryable: true,
          },
        };
      }
      if (!canConfirm(state, action.nowMs)) return state;
      return {
        ...state,
        phase: 'CONFIRMING',
        idempotencyKey: action.idempotencyKey,
        error: null,
      };
    case 'submitStarted':
      if (state.phase !== 'CONFIRMING') return state;
      return {...state, phase: 'SUBMITTING'};
    case 'submitSucceeded':
      if (state.phase !== 'SUBMITTING') return state;
      return {...state, phase: 'SUBMITTED', result: action.response};
    case 'submitFailed':
      if (state.phase !== 'SUBMITTING') return state;
      return {
        ...state,
        phase: 'FAILED',
        error: action.error,
        // A definitive rejection ends the action; an uncertain outcome keeps
        // the key so a retry is deduplicated by the server.
        idempotencyKey: action.error.retryable ? state.idempotencyKey : null,
        preview: action.error.retryable ? state.preview : null,
      };
    case 'reset':
      return initialTicketState(state.inputs);
    default:
      return state;
  }
}

/** Availability of an order mode for the selected account. */
export interface ModeAvailability {
  /** Mode. */
  mode: OrderMode;
  /** Whether it can be selected. */
  available: boolean;
  /** Why not, when unavailable. */
  reason: string | null;
}

/**
 * Computes which order modes the user may select.
 * LIVE requires the account's server-reported `modes` to include LIVE and
 * trading capability AVAILABLE; nothing in the browser can enable it.
 * @param account Account summary, or null when none is selected.
 * @return Availability for PAPER, SHADOW and LIVE.
 */
export function modeAvailability(
  account: AccountSummary | null
): ModeAvailability[] {
  const modes: OrderMode[] = ['PAPER', 'SHADOW', 'LIVE'];
  return modes.map(mode => {
    if (!account) {
      return {mode, available: false, reason: 'Choose an account first.'};
    }
    const status = accountStatusView(account);
    if (status.connection !== 'connected' && !account.modes.includes(mode)) {
      return {
        mode,
        available: false,
        reason: status.awaitingSdk
          ? `${status.venueName} is awaiting its broker API SDK; orders cannot be placed yet.`
          : `${status.venueName}: ${status.connectionText.toLowerCase()}. ${status.credentialsText}.`,
      };
    }
    if (!account.modes.includes(mode)) {
      return {
        mode,
        available: false,
        reason:
          mode === 'LIVE'
            ? 'Live trading is not enabled for this account. It needs server-side approval, verified venue permissions and explicit consent.'
            : `${mode.toLowerCase()} mode is not available for this account.`,
      };
    }
    if (mode === 'LIVE' && account.capabilities.status !== 'AVAILABLE') {
      return {
        mode,
        available: false,
        reason: `The venue adapter reports ${account.capabilities.status.replace(/_/g, ' ').toLowerCase()}.`,
      };
    }
    return {mode, available: true, reason: null};
  });
}

/**
 * Converts any thrown value into a ticket error.
 * @param err Thrown value.
 * @return Ticket error.
 */
export function toTicketError(err: unknown): TicketError {
  if (err instanceof ApiError) {
    return {
      code: err.body.code,
      message: err.body.message,
      retryable: err.body.retryable,
    };
  }
  return {
    code: 'NETWORK',
    message:
      'The request did not complete. The order may or may not have been received; retrying reuses the same key so it cannot be duplicated.',
    retryable: true,
  };
}

/** Dependencies of the ticket controller. */
export interface TicketControllerDeps {
  /** Execution client. */
  client: ExecutionClient;
  /** Returns the current time (UTC ms). */
  now?: () => number;
  /** Mints idempotency keys. */
  newKey?: () => string;
}

/**
 * Generates a random idempotency key.
 * @return UUID-like key.
 */
export function randomKey(): string {
  const cryptoApi = (globalThis as {crypto?: {randomUUID?: () => string}})
    .crypto;
  if (cryptoApi?.randomUUID) return cryptoApi.randomUUID();
  return `key-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

/**
 * Stateful controller wiring the reducer to the execution client.
 * Exposes `get`/`subscribe` so hosts can treat it as a store.
 */
export class TicketController {
  private state: TicketState;
  private readonly listeners = new Set<(state: TicketState) => void>();
  private instrument: InstrumentPrecision | null = null;
  private mark: Decimal | null = null;
  private context: TicketContext = {};
  private readonly now: () => number;
  private readonly newKey: () => string;

  /**
   * Creates a controller.
   * @param inputs Initial inputs.
   * @param deps Client, clock and key factory.
   */
  constructor(
    inputs: TicketInputs,
    private readonly deps: TicketControllerDeps
  ) {
    this.state = initialTicketState(inputs);
    this.now = deps.now ?? Date.now;
    this.newKey = deps.newKey ?? randomKey;
  }

  /**
   * Returns the current state.
   * @return Ticket state.
   */
  get(): TicketState {
    return this.state;
  }

  /**
   * Subscribes to state changes.
   * @param listener Listener.
   * @return Unsubscribe function.
   */
  subscribe(listener: (state: TicketState) => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  /**
   * Applies an action and notifies listeners on change.
   * @param action Action.
   */
  dispatch(action: TicketAction): void {
    const next = ticketReducer(this.state, action);
    if (next === this.state) return;
    this.state = next;
    for (const listener of [...this.listeners]) {
      listener(next);
    }
  }

  /**
   * Sets instrument precision and the reference mark price.
   * @param instrument Instrument precision.
   * @param mark Mark price, if known.
   */
  setMarket(
    instrument: InstrumentPrecision | null,
    mark: Decimal | null
  ): void {
    this.instrument = instrument;
    this.mark = mark;
  }

  /**
   * Sets the account and previous close used by cash-equity rules.
   * @param context Account and reference close.
   */
  setContext(context: TicketContext): void {
    this.context = context;
  }

  /**
   * Validates current inputs.
   * @return Build result.
   */
  build(): BuildResult {
    return buildIntentRequest(
      this.state.inputs,
      this.instrument,
      this.mark,
      this.context
    );
  }

  /**
   * Edits inputs (invalidates any preview).
   * @param patch Fields to change.
   */
  edit(patch: Partial<TicketInputs>): void {
    this.dispatch({type: 'edit', patch});
  }

  /**
   * Requests a server preview for the current inputs.
   * @return Promise resolving when the preview settles.
   */
  async preview(): Promise<void> {
    const built = this.build();
    if (!built.request) return;
    this.dispatch({type: 'previewRequested'});
    if (this.state.phase !== 'PREVIEWING') return;
    const revision = this.state.inputRevision;
    try {
      const preview = await this.deps.client.previewOrder(built.request);
      this.dispatch({type: 'previewSucceeded', preview, revision});
    } catch (err) {
      this.dispatch({
        type: 'previewFailed',
        error: toTicketError(err),
        revision,
      });
    }
  }

  /**
   * Confirms and submits the previewed order exactly once.
   * @return Promise resolving when submission settles.
   */
  async confirm(): Promise<void> {
    this.dispatch({
      type: 'confirm',
      nowMs: this.now(),
      idempotencyKey: this.newKey(),
    });
    if (this.state.phase !== 'CONFIRMING') return;
    const {preview, idempotencyKey} = this.state;
    if (!preview || !idempotencyKey) return;
    this.dispatch({type: 'submitStarted'});
    try {
      const response = await this.deps.client.submitIntent(
        {...preview.normalized, preview_token: preview.preview_token},
        idempotencyKey
      );
      this.dispatch({type: 'submitSucceeded', response});
    } catch (err) {
      this.dispatch({type: 'submitFailed', error: toTicketError(err)});
    }
  }
}
