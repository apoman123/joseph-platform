/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/** @fileoverview Provider labels and demo filtering in the feed status view. */

import type {VenueStatus} from '@joseph/contracts';
import {DemoMarketDataClient} from '@joseph/mock-provider';
import {describe, expect, it} from 'vitest';
import {
  displayedFeeds,
  feedSourceFor,
  groupedFeeds,
  visibleFeedState,
} from './data-health-feature';

describe('data feed status', () => {
  it('shows only real feeds when an old server also returns mock', async () => {
    const demo = (await new DemoMarketDataClient().listVenues()).venues[0];
    if (!demo) throw new Error('Demo venue is missing');
    const alpaca: VenueStatus = {
      ...demo,
      capabilities: {...demo.capabilities, venue: 'nasdaq'},
    };
    expect(displayedFeeds([demo, alpaca])).toEqual([alpaca]);
    expect(displayedFeeds([demo])).toEqual([demo]);
    expect(feedSourceFor(alpaca)).toBe('Alpaca IEX');
  });

  it('labels the selected Taiwan and rates sources', async () => {
    const demo = (await new DemoMarketDataClient().listVenues()).venues[0];
    if (!demo) throw new Error('Demo venue is missing');
    const taiwan: VenueStatus = {
      ...demo,
      capabilities: {
        ...demo.capabilities,
        venue: 'twse',
        display_name: 'Taiwan Stock Exchange (Fugle free tier)',
      },
    };
    const rates: VenueStatus = {
      ...demo,
      capabilities: {
        ...demo.capabilities,
        venue: 'treasury',
        notes: ['FRED_DGS* observations use the free FRED API.'],
      },
    };
    expect(feedSourceFor(taiwan)).toContain('Fugle');
    expect(feedSourceFor(rates)).toBe('U.S. Treasury · FRED');
  });

  it('groups five crypto markets under CCXT and marks unused REST feeds idle', async () => {
    const demo = (await new DemoMarketDataClient().listVenues()).venues[0];
    if (!demo) throw new Error('Demo venue is missing');
    const crypto = (
      ['binance', 'hyperliquid', 'lighter', 'bitget', 'mexc'] as const
    ).map(venue => ({
      ...demo,
      capabilities: {
        ...demo.capabilities,
        venue,
        display_name: `CCXT · ${venue}`,
      },
      health: {
        ...demo.health,
        feed_state: 'CONNECTING' as const,
        transport: 'NONE' as const,
        last_message_ms: null,
      },
    }));
    const official: VenueStatus = {
      ...demo,
      capabilities: {
        ...demo.capabilities,
        venue: 'twse',
        display_name: 'Taiwan Stock Exchange',
      },
    };
    expect(groupedFeeds([...crypto, official])).toHaveLength(1);
    expect(groupedFeeds([...crypto, official])[0]?.name).toBe('CCXT');
    expect(displayedFeeds([...crypto, official])).toHaveLength(5);
    const first = crypto[0];
    if (!first) throw new Error('CCXT fixture is empty');
    expect(visibleFeedState(first)).toBe('IDLE');
  });
});
