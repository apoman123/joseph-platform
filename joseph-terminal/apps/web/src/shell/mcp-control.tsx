/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/** @fileoverview Explicit MCP grants and opt-in, bounded browser automation. */

'use client';
import {
  HttpTransport,
  isDrawingContract,
  isWorkspaceContract,
} from '@joseph/contracts';
import {
  createCommand,
  updateCommand,
  deleteCommand,
  isImportableDrawing,
  DrawingRepository,
} from '@joseph/drawing-core';
import {
  activeChart,
  createTemplate,
  exportWorkspaceFile,
  parseWorkspaceFile,
  REPLAY_SPEEDS,
  type ReplayState,
} from '@joseph/terminal-shell';
import {
  createElement,
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactElement,
} from 'react';
import type {TerminalRuntime} from '../composition/runtime';
import {useStore, type WebFeature} from './react-bindings';
import {shellManifest} from '../features/shell/manifest';
import {
  importWorkspaceFile,
  screenshotCaption,
} from '../features/shell/chart-tools-feature';
import styles from './script-ui.module.css';
import {createStore, type WritableStore} from '@joseph/feature-registry';

/** A strict browser action received from the authenticated market service. */
interface BrowserAction {
  /** Allowlisted operation; never arbitrary browser JavaScript. */
  operation: string;
  /** Exact registered command identity when operation is command. */
  command_id?: string | null;
  /** Operation-specific JSON values, validated before mutation. */
  arguments: Record<string, unknown>;
}

/**
 * Captures explicit user context without tokens, client objects or browser cookies.
 * @param runtime Terminal state and registered capabilities.
 * @return JSON-safe context for the user's explicitly enabled agent connection.
 */
export function terminalSnapshot(
  runtime: TerminalRuntime
): Record<string, unknown> {
  return {
    workspace: runtime.workspace.get(),
    commands: runtime.commands.list().map(({id, title}) => ({id, title})),
    drawing_tools: runtime.tools.list().map(tool => tool.manifest),
    drawings: [...runtime.drawing.stores].map(([chartId, store]) => ({
      chart_id: chartId,
      drawings: store.get().drawings,
    })),
    theme: runtime.theme.get(),
    replay: runtime.replay.get(),
    templates: runtime.templates.list(),
    feed_states: runtime.feedStates.get(),
    operations: [
      'command',
      'workspace',
      'drawing',
      'replay',
      'theme',
      'export',
      'screenshot',
      'template',
    ],
    generated_at_ms: Date.now(),
  };
}

/**
 * Applies one explicit browser capability with runtime contract validation.
 * @param runtime Opted-in user terminal; all existing broker gates remain active.
 * @param action Validated transport operation and untrusted JSON arguments.
 * @return Structured completion/artifact; rejects uncertain or unsupported actions.
 * @throws Error For invalid documents, unavailable charts or native gesture needs.
 */
export async function applyTerminalAction(
  runtime: TerminalRuntime,
  action: BrowserAction
): Promise<unknown> {
  const args = action.arguments;
  const workspace = runtime.workspace.get();
  const chart =
    workspace.charts.find(row => row.chart_id === args.chart_id) ??
    activeChart(workspace);
  if (action.operation === 'command') {
    const id = action.command_id;
    if (!id || !runtime.commands.list().some(command => command.id === id)) {
      throw new Error(
        'Choose an exact command id from this browser capability catalog.'
      );
    }
    if (
      ['workspace.export', 'workspace.share', 'chart.screenshot'].includes(id)
    ) {
      throw new Error(
        'Use export or screenshot operations to return an artifact; clipboard and downloads require a user gesture.'
      );
    }
    if (/popout|detach|notification.*permission/i.test(id)) {
      throw new Error(
        'This browser operation requires an explicit user gesture in the terminal.'
      );
    }
    await runtime.commands.execute(id, args);
  } else if (action.operation === 'workspace') {
    if (typeof args.file === 'string') {
      const result = parseWorkspaceFile(args.file, Date.now());
      if (!result.ok) throw new Error(result.error);
      if (!isWorkspaceContract(result.workspace)) {
        throw new Error(
          'Imported workspace does not satisfy the canonical schema.'
        );
      }
      importWorkspaceFile(runtime, result);
    } else {
      if (!isWorkspaceContract(args.document)) {
        throw new Error(
          'document must satisfy the complete WorkspaceDocument schema.'
        );
      }
      const doc = args.document;
      if (
        new Set(doc.charts.map(row => row.chart_id)).size !==
          doc.charts.length ||
        !doc.charts.some(row => row.chart_id === doc.active_chart_id)
      ) {
        throw new Error(
          'Workspace requires unique chart ids and a valid active chart.'
        );
      }
      runtime.dispatch({
        type: 'replace',
        workspace: {...doc, workspace_id: workspace.workspace_id},
      });
    }
  } else if (action.operation === 'theme') {
    if (args.theme !== 'light' && args.theme !== 'dark') {
      throw new Error('theme must be light or dark.');
    }
    runtime.theme.set(args.theme);
  } else if (action.operation === 'drawing') {
    const store = runtime.drawing.stores.get(chart.chart_id);
    if (!store) {
      throw new Error(
        'The target candle chart is not mounted; select its tab first.'
      );
    }
    if (args.action === 'undo') store.undo();
    else if (args.action === 'redo') store.redo();
    else if (args.action === 'select') {
      if (
        args.drawing_id !== null &&
        (typeof args.drawing_id !== 'string' || !store.find(args.drawing_id))
      ) {
        throw new Error('Choose an existing drawing_id or null.');
      }
      runtime.drawing.controllers.get(chart.chart_id)?.select(args.drawing_id);
    } else if (args.action === 'delete') {
      const before =
        typeof args.drawing_id === 'string'
          ? store.find(args.drawing_id)
          : undefined;
      if (!before) throw new Error('Owned chart drawing not found.');
      if (before.locked) {
        throw new Error('Unlock the drawing before deleting it.');
      }
      store.execute(deleteCommand(before));
    } else {
      const doc = args.document;
      if (
        !isDrawingContract(doc) ||
        !isImportableDrawing(doc, chart.instrument_id)
      ) {
        throw new Error(
          'document must be a valid drawing for the selected chart instrument.'
        );
      }
      const tool = runtime.tools.get(doc.tool_id);
      if (!tool || doc.anchors.length < tool.manifest.anchorCount) {
        throw new Error(
          'Drawing tool is absent or its required anchors are missing.'
        );
      }
      const before = store.find(doc.drawing_id);
      if (args.action === 'create') {
        if (before) throw new Error('Drawing id already exists.');
        store.execute(createCommand(doc));
      } else if (args.action === 'update') {
        if (!before) throw new Error('Drawing not found.');
        if (
          before.locked &&
          (doc.locked ||
            JSON.stringify({
              ...before,
              locked: false,
              updated_at_ms: doc.updated_at_ms,
            }) !== JSON.stringify(doc))
        ) {
          throw new Error(
            'Only an explicit unlock may update a locked drawing.'
          );
        }
        store.execute(updateCommand(before, doc, 'MCP drawing edit'));
      } else {
        throw new Error(
          'Drawing action must be create, update, delete, select, undo or redo.'
        );
      }
    }
  } else if (action.operation === 'replay') {
    if (args.state === null) {
      runtime.replay.update(all => {
        const {[chart.chart_id]: ended, ...rest} = all;
        void ended;
        return rest;
      });
    } else {
      const value = args.state as Record<string, unknown> | undefined;
      if (
        !value ||
        (value.phase !== 'select' &&
          value.phase !== 'paused' &&
          value.phase !== 'playing') ||
        typeof value.speed !== 'number' ||
        !REPLAY_SPEEDS.includes(value.speed) ||
        (value.cursorMs !== null &&
          (typeof value.cursorMs !== 'number' ||
            !Number.isSafeInteger(value.cursorMs) ||
            value.cursorMs < 0))
      ) {
        throw new Error(
          'state requires phase select/paused/playing, cursorMs (epoch ms or null), speed 1/2/5/10; null exits replay.'
        );
      }
      const next: ReplayState = {
        phase: value.phase,
        cursorMs: value.cursorMs,
        speed: value.speed,
      };
      runtime.replay.update(all => ({...all, [chart.chart_id]: next}));
    }
  } else if (action.operation === 'template') {
    const id = String(args.template_id ?? '');
    if (args.action === 'save') {
      if (!id || typeof args.name !== 'string') {
        throw new Error('Provide template_id and name.');
      }
      runtime.templates.save(createTemplate(chart, args.name, id, Date.now()));
    } else if (args.action === 'delete') runtime.templates.remove(id);
    else if (args.action === 'apply') {
      const found = runtime.templates
        .list()
        .find(row => row.template_id === id);
      if (!found) throw new Error('Template not found.');
      runtime.dispatch({
        type: 'applyChartPresentation',
        chartId: chart.chart_id,
        presentation: found.presentation,
      });
    } else throw new Error('Template action must be save, apply or delete.');
  } else if (action.operation === 'export') {
    const repository = new DrawingRepository(
      runtime.storage,
      workspace.workspace_id,
      runtime.tools
    );
    for (const row of workspace.charts) {
      const store = runtime.drawing.stores.get(row.chart_id);
      if (store) repository.save(row.instrument_id, store.get());
    }
    const saved = repository.exportAll();
    return {
      mime_type: 'application/json',
      name: 'joseph-workspace.json',
      text: exportWorkspaceFile(workspace, saved.collections, Date.now()),
    };
  } else if (action.operation === 'screenshot') {
    const png = runtime.charts.get(chart.chart_id)?.screenshot();
    if (!png) throw new Error('Mounted chart is not ready for a screenshot.');
    return {
      mime_type: 'image/png',
      data_url: png,
      caption: screenshotCaption(
        chart,
        runtime.feedStates.get()[chart.chart_id] ?? 'UNAVAILABLE',
        Date.now()
      ),
    };
  } else {
    throw new Error(
      'Unsupported browser operation. Arbitrary JavaScript is never accepted.'
    );
  }
  runtime.flush();
  return {ok: true, state: terminalSnapshot(runtime)};
}

/** Runtime-specific browser control state; it survives switching panels. */
const browserControls = new WeakMap<
  TerminalRuntime,
  WritableStore<{enabled: boolean; status: string; error: string | null}>
>();

/**
 * Returns the current user's explicit browser control switch and connection state.
 * @param runtime Current browser runtime.
 * @return Stable control state, initially disabled on every fresh runtime.
 */
function browserControl(
  runtime: TerminalRuntime
): WritableStore<{enabled: boolean; status: string; error: string | null}> {
  let state = browserControls.get(runtime);
  if (!state) {
    state = createStore({
      enabled: false,
      status: 'Browser control off',
      error: null as string | null,
    });
    browserControls.set(runtime, state);
  }
  return state;
}

/**
 * Connects opted-in control until its feature is disabled or the window closes.
 * @param runtime Authenticated terminal runtime.
 * @param setStatus Reports connection progress without changing user permission.
 * @param setError Reports bounded transport or action failures.
 * @return Cleanup closing the socket and all reconnect/heartbeat timers.
 */
function connectBrowserControl(
  runtime: TerminalRuntime,
  setStatus: (value: string) => void,
  setError: (value: string) => void
): () => void {
  if (!runtime.config.marketWsUrl) return () => undefined;
  let disposed = false;
  let socket: WebSocket;
  let retry: ReturnType<typeof setTimeout>;
  let heartbeat: ReturnType<typeof setInterval>;
  const connect = () => {
    socket = new WebSocket(`${runtime.config.marketWsUrl}/v1/terminal/control`);
    setStatus('Connecting browser control…');
    socket.onopen = () =>
      void socket.send(JSON.stringify({token: runtime.config.devToken}));
    const publish = () => {
      if (socket.readyState === WebSocket.OPEN) {
        const message = JSON.stringify({
          type: 'state',
          state: terminalSnapshot(runtime),
        });
        if (message.length < 2000000) socket.send(message);
        else {
          setStatus('Workspace exceeds the 2 MB browser control context limit');
        }
      }
    };
    socket.onmessage = event => {
      void (async () => {
        const message = JSON.parse(String(event.data)) as Record<
          string,
          unknown
        >;
        if (message.type === 'connected') {
          setStatus(
            `Browser control connected · ${String(message.session_id)}`
          );
          publish();
          heartbeat = setInterval(publish, 20000);
          return;
        }
        if (
          message.type !== 'action' ||
          typeof message.request_id !== 'string'
        ) {
          return;
        }
        try {
          if (
            !message.arguments ||
            typeof message.arguments !== 'object' ||
            Array.isArray(message.arguments)
          ) {
            throw new Error('Invalid browser action arguments.');
          }
          const result = await applyTerminalAction(
            runtime,
            message as unknown as BrowserAction
          );
          if (!disposed && socket.readyState === WebSocket.OPEN) {
            const response = JSON.stringify({
              type: 'result',
              request_id: message.request_id,
              ok: true,
              result,
            });
            socket.send(
              response.length <= 8000000
                ? response
                : JSON.stringify({
                    type: 'result',
                    request_id: message.request_id,
                    ok: false,
                    error: 'Result exceeds the 8 MB browser action limit',
                  })
            );
            publish();
          }
        } catch (failure) {
          if (!disposed && socket.readyState === WebSocket.OPEN) {
            socket.send(
              JSON.stringify({
                type: 'result',
                request_id: message.request_id,
                ok: false,
                error:
                  failure instanceof Error ? failure.message : String(failure),
              })
            );
          }
        }
      })().catch(failure => void setError(String(failure)));
    };
    socket.onclose = () => {
      clearInterval(heartbeat);
      if (!disposed) {
        setStatus('Browser control disconnected; reconnecting…');
        retry = setTimeout(connect, 3000);
      }
    };
  };
  connect();
  return () => {
    disposed = true;
    clearTimeout(retry);
    clearInterval(heartbeat);
    socket.close();
  };
}

/**
 * Renders explicit resource-token grants and optional browser action controls.
 * @param props User terminal whose backend starts MCP automatically.
 * @return Credential setup, revocation and visible browser-control status.
 */
export function McpControl({
  runtime,
}: {
  runtime: TerminalRuntime;
}): ReactElement {
  const http = useMemo(
    () =>
      new HttpTransport({
        baseUrl: runtime.config.marketApiUrl ?? '',
        getToken: () => runtime.config.devToken,
      }),
    [runtime]
  );
  const [connections, setConnections] = useState<
    Array<{
      connection_id: string;
      name: string;
      scopes: string[];
      revoked: boolean;
    }>
  >([]);
  const [token, setToken] = useState<string | null>(null);
  const [url, setUrl] = useState('');
  const [name, setName] = useState('My MCP client');
  const [write, setWrite] = useState(false);
  const [trade, setTrade] = useState(false);
  const control = browserControl(runtime);
  const {enabled, status, error: controlError} = useStore(control);
  const setEnabled = (value: boolean) =>
    void control.update(current => ({...current, enabled: value}));
  const setStatus = (value: string) =>
    void control.update(current => ({...current, status: value}));
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    const data = await http.request<{
      connections: typeof connections;
      url: string;
    }>('GET', '/v1/mcp/connections');
    setConnections(data.connections);
    setUrl(data.url);
  }, [http]);
  useEffect(() => {
    if (!runtime.config.demo) {
      void load().catch(failure => void setError(String(failure)));
    }
  }, [runtime, load]);
  return (
    <section className={styles.editor} aria-label="MCP settings">
      <h2>Model Context Protocol</h2>
      <p>
        The MCP endpoint starts with the market server. This resource uses
        explicit bearer grants. Configure your MCP client with the endpoint and
        token below.
      </p>
      {runtime.config.demo ? (
        <p>Connect the market engine to configure MCP.</p>
      ) : (
        <>
          <p>
            Endpoint: <code>{url || `${runtime.config.marketApiUrl}/mcp`}</code>
          </p>
          <div className={styles.toolbar}>
            <label>
              Name
              <input
                className="j-input"
                value={name}
                onChange={event => void setName(event.target.value)}
              />
            </label>
            <label className="j-check">
              <input
                type="checkbox"
                checked={write}
                onChange={event => {
                  setWrite(event.target.checked);
                  if (!event.target.checked) setTrade(false);
                }}
              />
              Allow writes
            </label>
            <label className="j-check">
              <input
                type="checkbox"
                checked={trade}
                disabled={!write}
                onChange={event => void setTrade(event.target.checked)}
              />
              Allow trading (existing account and LIVE consent gates apply)
            </label>
            <button
              className="j-btn"
              onClick={() => {
                void http
                  .request<{token: string}>(
                    'POST',
                    '/v1/mcp/connections',
                    undefined,
                    {name, write, trade}
                  )
                  .then(
                    async grant => {
                      setToken(grant.token);
                      await load();
                    },
                    failure => void setError(String(failure))
                  );
              }}
            >
              Create MCP connection
            </button>
          </div>
          {token && (
            <label className={styles.field}>
              One-time MCP token
              <textarea className="j-input" readOnly value={token} />
              <button className="j-btn" onClick={() => void setToken(null)}>
                Hide token
              </button>
            </label>
          )}
          {connections.map(row => (
            <div key={row.connection_id} className={styles.row}>
              <span>
                {row.name} · {row.scopes.join(', ')} ·{' '}
                {row.revoked ? 'Revoked' : 'Active'}
              </span>
              <button
                className="j-btn"
                disabled={row.revoked}
                onClick={() =>
                  void http
                    .request(
                      'DELETE',
                      `/v1/mcp/connections/${row.connection_id}`
                    )
                    .then(load, failure => void setError(String(failure)))
                }
              >
                Revoke
              </button>
            </div>
          ))}
          <label className="j-check">
            <input
              type="checkbox"
              checked={enabled}
              onChange={event => {
                setEnabled(event.target.checked);
                if (!event.target.checked) setStatus('Browser control off');
              }}
            />
            Enable MCP browser control for this window
          </label>
          <p role="status">{status}</p>
          <p className={styles.notice}>
            Turning this off or closing the window disconnects browser control.
            Backend data, scripts, alerts and trading API tools remain available
            under their connection grants. Browser gestures such as clipboard,
            pop-outs and permission dialogs require direct user interaction.
          </p>
        </>
      )}
      {(error || controlError) && <p role="alert">{error || controlError}</p>}
    </section>
  );
}

/**
 * Registers user-controlled MCP setup and opt-in browser automation.
 * @param runtime Current terminal runtime.
 * @return Removable settings panel and discoverable command.
 */
export function mcpFeature(runtime: TerminalRuntime): WebFeature {
  return {
    manifest: shellManifest(
      'shell.mcp',
      'MCP',
      'Resource-scoped agent connections and opt-in terminal control.',
      {uiSlots: ['bottom-panel']}
    ),
    activate(ctx) {
      const control = browserControl(runtime);
      let previousEnabled = false;
      let stop: (() => void) | null = null;
      const unsubscribe = control.subscribe(value => {
        if (value.enabled === previousEnabled) return;
        previousEnabled = value.enabled;
        if (value.enabled) {
          stop = connectBrowserControl(
            runtime,
            status => void control.update(current => ({...current, status})),
            error => void control.update(current => ({...current, error}))
          );
        } else {
          stop?.();
          stop = null;
        }
      });
      ctx.commands.register({
        id: 'shell.mcp',
        title: 'Open MCP settings',
        run: () =>
          ctx.commands.execute('shell.showPanel', {
            slot: 'bottom-panel',
            id: 'shell.mcp',
          }),
      });
      const removePanel = ctx.slots.contribute('bottom-panel', {
        id: 'shell.mcp',
        title: 'MCP',
        order: 25,
        icon: 'plug',
        view: () => createElement(McpControl, {runtime}),
      });
      return () => {
        unsubscribe();
        stop?.();
        removePanel();
        control.set({
          enabled: false,
          status: 'Browser control off',
          error: null,
        });
      };
    },
  };
}
