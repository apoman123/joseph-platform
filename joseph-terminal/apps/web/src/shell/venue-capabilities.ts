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
 * @param runtime Runtime.
 * @return Capabilities by venue.
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
    promise.catch(() => cache.delete(runtime));
    cache.set(runtime, promise);
  }
  return promise;
}

/**
 * Capabilities of one venue.
 * @param runtime Runtime.
 * @param venue Venue.
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
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [runtime, venue]);
  return capabilities;
}
