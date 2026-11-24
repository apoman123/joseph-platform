/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Venue capabilities published by the market engine (or the
 * DEMO provider), cached once per runtime. Used to grey out timeframes a
 * venue does not serve; while unknown, nothing is hidden and the engine
 * remains the authority (it refuses what it does not serve).
 */

'use client';
import type {Venue, VenueCapabilities} from '@joseph/contracts';
import {useEffect, useState} from 'react';
import type {TerminalRuntime} from '../composition/runtime';

/** Capabilities by venue, cached per runtime. */
const cache = new WeakMap<
  TerminalRuntime,
  Promise<ReadonlyMap<Venue, VenueCapabilities>>
>();

/**
 * Loads venue capabilities once per runtime (retried after a failure).
 * @param runtime Runtime whose market client is asked.
 * @return Capabilities by venue; rejects when the venue list fails to load.
 */
export function loadVenueCapabilities(
  runtime: TerminalRuntime
): Promise<ReadonlyMap<Venue, VenueCapabilities>> {
  let promise = cache.get(runtime);
  if (!promise) {
    promise = runtime.clients.market
      .listVenues()
      .then(
        list =>
          new Map(
            list.venues.map(status => [
              status.capabilities.venue,
              status.capabilities,
            ])
          )
      );
    // Forget a failed load so the next caller retries.
    promise.catch(() => cache.delete(runtime));
    cache.set(runtime, promise);
  }
  return promise;
}

/**
 * Returns the capabilities of one venue, loading them on first use.
 * @param runtime Runtime whose market client is asked.
 * @param venue Venue to look up.
 * @return Capabilities, or null while loading or unavailable.
 */
export function useVenueCapabilities(
  runtime: TerminalRuntime,
  venue: Venue
): VenueCapabilities | null {
  const [capabilities, setCapabilities] = useState<VenueCapabilities | null>(
    null
  );
  useEffect(() => {
    let alive = true;
    setCapabilities(null);
    loadVenueCapabilities(runtime)
      .then(map => {
        if (alive) setCapabilities(map.get(venue) ?? null);
      })
      // Unknown capabilities grey out nothing; the engine stays the
      // authority.
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [runtime, venue]);
  return capabilities;
}
