/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Runs a market filter scan (`ScriptsClient.screen`) and polls
 * its progress until it completes, fails or is cancelled. Shared by the
 * screener and the watchlist's qualified list.
 */

'use client';
import type {ScanInput, ScanJob, ScriptsClient} from '@joseph/contracts';
import {useCallback, useEffect, useRef, useState} from 'react';
import {errorMessage} from './hooks';

/** Poll interval while a scan runs. */
export const SCAN_POLL_MS = 1000;

/** State and controls of one scan. */
export interface ScanState {
  /** Latest job state, or null before the first scan. */
  job: ScanJob | null;
  /** Message of the last failed request, or null. */
  error: string | null;
  /** Whether a scan is pending or running. */
  running: boolean;
  /**
   * Starts a new scan, replacing the current one.
   * @param body Scan request.
   * @return Resolves once the job was submitted (or failed).
   */
  start(body: ScanInput): Promise<void>;
  /**
   * Cancels the running scan, keeping its partial results.
   * @return Resolves after the request.
   */
  cancel(): Promise<void>;
}

/**
 * Tells whether a scan is still working.
 * @param job Job or null.
 * @return True while pending or running.
 */
export function isActive(job: ScanJob | null): boolean {
  return !!job && (job.status === 'pending' || job.status === 'running');
}

/**
 * Submits scans and polls the active one.
 * @param scripts Scripts client.
 * @return Scan state and controls.
 */
export function useScanJob(scripts: ScriptsClient): ScanState {
  const [job, setJob] = useState<ScanJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The latest submission wins; older polls are ignored.
  const current = useRef<string | null>(null);
  useEffect(() => {
    if (!job || !isActive(job)) return;
    let alive = true;
    const timer = setTimeout(() => {
      scripts.scanStatus(job.job_id).then(
        next => {
          if (alive && current.current === next.job_id) setJob(next);
        },
        (err: unknown) => {
          if (alive) setError(errorMessage(err));
        }
      );
    }, SCAN_POLL_MS);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [scripts, job]);
  const start = useCallback(
    async (body: ScanInput) => {
      setError(null);
      try {
        const next = await scripts.screen(body);
        current.current = next.job_id;
        setJob(next);
      } catch (err) {
        setError(errorMessage(err));
      }
    },
    [scripts]
  );
  const cancel = useCallback(async () => {
    if (!job) return;
    try {
      setJob(await scripts.cancelScan(job.job_id));
    } catch (err) {
      setError(errorMessage(err));
    }
  }, [scripts, job]);
  return {job, error, running: isActive(job), start, cancel};
}
