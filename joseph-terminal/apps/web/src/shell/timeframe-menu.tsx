/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Timeframe control shared by the top bar and pop-out
 * windows: favorites, a grouped menu (seconds, minutes, hours, days) and a
 * typed resolution box. Timeframes the chart's venue does not publish are
 * disabled with the reason; the engine stays the authority.
 *
 * Favorites and menu entries are toggle buttons (`aria-pressed` marks the
 * chart's timeframe); the reason a timeframe is disabled is its tooltip.
 * The typed box describes itself through `aria-describedby` and announces
 * an invalid entry with `role="alert"`.
 */

'use client';
import {
  TIMEFRAMES,
  parseInstrumentId,
  type ChartState,
  type Timeframe,
} from '@joseph/contracts';
import {
  TIMEFRAME_GROUPS,
  offeredTimeframes,
  parseTimeframeInput,
  timeframeAvailability,
  timeframeTitle,
} from '@joseph/terminal-shell';
import {Star} from 'lucide-react';
import {useState, type ReactElement} from 'react';
import {Popover} from './popover';
import {useStore} from './react-bindings';
import {useRuntime} from './runtime-context';
import {useVenueCapabilities} from './venue-capabilities';

/**
 * Renders the custom resolution input ("15", "4h", "1D", "5S", "1M").
 * @param props Offered timeframes (null while unknown) and the apply
 *     callback.
 * @return Form element.
 */
function CustomTimeframe({
  offered,
  onApply,
}: {
  offered: ReadonlySet<Timeframe> | null;
  onApply: (timeframe: Timeframe) => void;
}): ReactElement {
  const [text, setText] = useState('');
  const [error, setError] = useState<string | null>(null);
  return (
    <form
      className="tf-custom"
      onSubmit={event => {
        event.preventDefault();
        const parsed = parseTimeframeInput(text, offered);
        if (parsed.ok) {
          setError(null);
          setText('');
          onApply(parsed.timeframe);
        } else {
          setError(parsed.error);
        }
      }}
    >
      <label className="j-label" htmlFor="tf-custom">
        Type a resolution
      </label>
      <div className="tf-custom__row">
        <input
          id="tf-custom"
          className="j-input"
          placeholder="15, 4h, 5S, 1M"
          value={text}
          aria-invalid={error ? true : undefined}
          aria-describedby="tf-custom-help"
          onChange={event => void setText(event.target.value)}
        />
        <button type="submit" className="j-btn j-btn--sm">
          Go
        </button>
      </div>
      <p
        id="tf-custom-help"
        className={error ? 'tf-custom__error' : 'tf-custom__help'}
        role={error ? 'alert' : undefined}
      >
        {error ?? 'A bare number means minutes. S is seconds, M is months.'}
      </p>
    </form>
  );
}

/** Props of the timeframe control. */
export interface TimeframeControlProps {
  /** Chart whose timeframe is shown and changed. */
  chart: ChartState;
}

/**
 * Renders the favorite timeframes plus the full timeframe menu for one
 * chart.
 * @param props Chart.
 * @return Navigation element.
 */
export function TimeframeControl({chart}: TimeframeControlProps): ReactElement {
  const runtime = useRuntime();
  const workspace = useStore(runtime.workspace);
  const favorites = workspace.favorites?.timeframes ?? [];
  const chartId = chart.chart_id;
  const {venue} = parseInstrumentId(chart.instrument_id);
  const capabilities = useVenueCapabilities(runtime, venue);
  const offered = offeredTimeframes(capabilities);
  const select = (timeframe: Timeframe) =>
    void runtime.dispatch({type: 'setTimeframe', chartId, timeframe});
  return (
    <nav className="timeframes" aria-label="Timeframe">
      {favorites.map(tf => {
        const availability = timeframeAvailability(tf, capabilities);
        return (
          <button
            key={tf}
            type="button"
            className="timeframes__item"
            aria-pressed={chart.timeframe === tf}
            disabled={!availability.offered}
            title={availability.note}
            onClick={() => void select(tf)}
          >
            {tf}
          </button>
        );
      })}
      <Popover
        ariaLabel="All timeframes"
        label={
          <span title={timeframeTitle(chart.timeframe)}>
            {favorites.includes(chart.timeframe) ? 'More' : chart.timeframe}
          </span>
        }
        className="timeframes__item timeframes__more"
      >
        {close => (
          <div className="tf-menu">
            <CustomTimeframe
              offered={offered}
              onApply={timeframe => {
                select(timeframe);
                close();
              }}
            />
            <div className="tf-menu__groups">
              {TIMEFRAME_GROUPS.map(group => (
                <section
                  key={group.label}
                  className="tf-group"
                  aria-label={group.label}
                >
                  <h3 className="tf-group__label">{group.label}</h3>
                  {group.timeframes.map(tf => {
                    const availability = timeframeAvailability(
                      tf,
                      capabilities
                    );
                    const favorite = favorites.includes(tf);
                    return (
                      <div key={tf} className="tf-grid__row">
                        <button
                          type="button"
                          className="j-menu__item"
                          aria-pressed={chart.timeframe === tf}
                          disabled={!availability.offered}
                          title={availability.note}
                          data-testid={`timeframe-${tf}`}
                          onClick={() => {
                            select(tf);
                            close();
                          }}
                        >
                          {tf}
                          {availability.tradeBuilt && (
                            <span className="tf-grid__note">trades</span>
                          )}
                        </button>
                        <button
                          type="button"
                          className="j-btn j-btn--ghost j-btn--icon j-btn--sm"
                          aria-label={
                            favorite
                              ? `Remove ${tf} from favorites`
                              : `Add ${tf} to favorites`
                          }
                          aria-pressed={favorite}
                          onClick={() =>
                            void runtime.dispatch({
                              type: 'toggleFavoriteTimeframe',
                              timeframe: tf,
                            })
                          }
                        >
                          <Star
                            size={13}
                            fill={favorite ? 'currentColor' : 'none'}
                            aria-hidden="true"
                          />
                        </button>
                      </div>
                    );
                  })}
                </section>
              ))}
            </div>
            {capabilities && offered && offered.size < TIMEFRAMES.length && (
              <p className="tf-menu__note">
                Dimmed timeframes are not published by{' '}
                {capabilities.display_name}.
              </p>
            )}
          </div>
        )}
      </Popover>
    </nav>
  );
}
