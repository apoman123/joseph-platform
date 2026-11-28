/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/** @fileoverview Typed managed Python scripts, scans, alerts and heatmaps. */

import type {
  ScriptInput,
  ScriptVersion,
  ScriptRun,
  ScriptResult,
  ScanInput,
  ScanJob,
  AlertJobInput,
  AlertJob,
  HeatmapCatalog,
  HeatmapSnapshot,
  Signal,
  CatalogList,
  CatalogScript,
} from '../generated/contracts';
import {HttpTransport, type HttpClientOptions} from './http';
import type {PythonCatalog, ScriptsClient} from './ports';

/** Heatmap request: family, optional data set and optional tile limit. */
export interface HeatmapQuery {
  /** Heatmap family. */
  type: 'stocks' | 'etfs' | 'crypto';
  /** Data set id of that family; the engine picks its default when absent. */
  dataset?: string;
  /** Maximum tiles; the data set's own size when absent. */
  limit?: number;
}

/** Authenticated managed-code workflows; never sends broker credentials. */
export class PythonClient implements ScriptsClient {
  private readonly http: HttpTransport;

  /**
   * Creates the client with the existing application's user credential.
   * @param options Fixed market URL, token provider and optional fetch adapter.
   */
  constructor(options: HttpClientOptions) {
    this.http = new HttpTransport(options);
  }

  /**
   * Lists the tenant's latest sources and runtime state.
   * @param offset Nonnegative source offset, default the first bounded page.
   * @return Owned source catalog; rejects on authentication or network errors.
   */
  list(offset = 0): Promise<PythonCatalog> {
    return this.http.request('GET', '/v1/scripts', {offset});
  }

  /**
   * Reads one immutable owned source version, including archived pins.
   * @param id Owned script slug.
   * @param version Exact immutable version.
   * @return Source version; rejects if it is unavailable to this tenant.
   */
  get(id: string, version: number): Promise<ScriptVersion> {
    return this.http.request(
      'GET',
      `/v1/scripts/${encodeURIComponent(id)}/versions/${version}`
    );
  }

  /**
   * Validates declarations and pinned imports without executing Python.
   * @param body Source and configuration to check.
   * @return Validation result; rejects with an actionable source error.
   */
  validate(body: ScriptInput): Promise<{valid: boolean; executed: boolean}> {
    return this.http.request('POST', '/v1/scripts/validate', undefined, body);
  }

  /**
   * Creates a source or appends a version using optimistic locking.
   * @param body New source with expected_version when editing.
   * @return Immutable saved version; rejects on stale edits or invalid source.
   */
  save(body: ScriptInput): Promise<ScriptVersion> {
    return this.http.request(
      'POST',
      body.expected_version
        ? `/v1/scripts/${encodeURIComponent(body.script_id)}/versions`
        : '/v1/scripts',
      undefined,
      body
    );
  }

  /**
   * Archives the visible head while retaining existing immutable imports.
   * @param id Owned script slug.
   * @return Resolves after archival; rejects on access or missing source.
   */
  archive(id: string): Promise<void> {
    return this.http.request('DELETE', `/v1/scripts/${encodeURIComponent(id)}`);
  }

  /**
   * Evaluates closed source bars without placing real orders or notifications.
   * @param id Owned runnable script slug.
   * @param body Immutable version, source range and input overrides.
   * @return Validated plots, drawings, simulated fills and source provenance.
   */
  run(id: string, body: ScriptRun): Promise<ScriptResult> {
    return this.http.request(
      'POST',
      `/v1/scripts/${encodeURIComponent(id)}/run`,
      undefined,
      body
    );
  }

  /**
   * Lists built-in and installed plugin scripts.
   * @param kind Optional role filter.
   * @return Catalog listing without source text.
   */
  catalog(kind?: CatalogScript['kind']): Promise<CatalogList> {
    return this.http.request('GET', '/v1/scripts/catalog', {kind});
  }

  /**
   * Starts a scan of the body's conditions combined with AND
   * (`POST /v1/script-scans`).
   * @param body Market selection, bounds and one to ten conditions.
   * @return Pending job for progress polling.
   */
  screen(body: ScanInput): Promise<ScanJob> {
    return this.http.request('POST', '/v1/script-scans', undefined, body);
  }

  /**
   * Lists custom alert signals (`GET /v1/signals?kind=CUSTOM_ALERT`).
   * @param limit Maximum rows, default two hundred.
   * @return Alert signals, newest first.
   */
  async alertLog(limit = 200): Promise<Signal[]> {
    const list = await this.http.request<{signals: Signal[]}>(
      'GET',
      '/v1/signals',
      {kind: 'CUSTOM_ALERT', limit}
    );
    return list.signals;
  }

  /**
   * Starts a bounded asynchronous market scan.
   * @param id Owned Filter script slug.
   * @param body Real market selection and explicit work limits.
   * @return Pending job for progress polling.
   */
  scan(id: string, body: ScanInput): Promise<ScanJob> {
    return this.http.request(
      'POST',
      `/v1/scripts/${encodeURIComponent(id)}/scan`,
      undefined,
      body
    );
  }

  /**
   * Reads persisted scan progress and actual matching symbols.
   * @param id Owned job identifier.
   * @return Current matches, errors and progress.
   */
  scanStatus(id: string): Promise<ScanJob> {
    return this.http.request(
      'GET',
      `/v1/script-scans/${encodeURIComponent(id)}`
    );
  }

  /**
   * Cancels a scan while keeping completed results.
   * @param id Owned job identifier.
   * @return Terminal job state.
   */
  cancelScan(id: string): Promise<ScanJob> {
    return this.http.request(
      'POST',
      `/v1/script-scans/${encodeURIComponent(id)}/cancel`
    );
  }

  /**
   * Lists pinned live conditions and their evaluation diagnostics.
   * @return Owned alert jobs, including disabled jobs.
   */
  alerts(): Promise<AlertJob[]> {
    return this.http.request('GET', '/v1/script-alerts');
  }

  /**
   * Creates or replaces a user-enabled future-bar condition.
   * @param body Pinned source, symbol and selected notification destinations.
   * @param id Optional owned job identifier for replacement.
   * @return Persistent condition state; rejects on invalid access or runtime.
   */
  saveAlert(body: AlertJobInput, id?: string): Promise<AlertJob> {
    return this.http.request(
      id ? 'PUT' : 'POST',
      `/v1/script-alerts${id ? `/${encodeURIComponent(id)}` : ''}`,
      undefined,
      body
    );
  }

  /**
   * Removes a condition without removing notification history.
   * @param id Owned condition identifier.
   * @return Resolves after removal.
   */
  deleteAlert(id: string): Promise<void> {
    return this.http.request(
      'DELETE',
      `/v1/script-alerts/${encodeURIComponent(id)}`
    );
  }

  /**
   * Loads one heatmap data set within the engine's short time budget.
   * @param query Heatmap family, optional data set id and tile limit.
   * @param signal Optional caller cancellation signal.
   * @return Tiles with available metrics, coverage and provenance.
   */
  heatmap(query: HeatmapQuery, signal?: AbortSignal): Promise<HeatmapSnapshot> {
    return this.http.request(
      'GET',
      '/v1/heatmap',
      {type: query.type, dataset: query.dataset, limit: query.limit},
      undefined,
      undefined,
      signal
    );
  }

  /**
   * Lists every heatmap data set with its availability.
   * @param signal Optional caller cancellation signal.
   * @return Data sets in menu order and the default per family.
   */
  heatmapCatalog(signal?: AbortSignal): Promise<HeatmapCatalog> {
    return this.http.request(
      'GET',
      '/v1/heatmap/catalog',
      undefined,
      undefined,
      undefined,
      signal
    );
  }

  /**
   * Reads public built-in indicator source; external protected code is refused.
   * @param id Registered built-in identifier.
   * @return Read-only Python source and public availability label.
   */
  source(id: string): Promise<{source: string; availability: string}> {
    return this.http.request(
      'GET',
      `/v1/indicators/plugins/${encodeURIComponent(id)}/source`
    );
  }
}
