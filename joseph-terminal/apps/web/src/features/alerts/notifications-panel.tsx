/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Notification subscriptions: where (Telegram, LINE, e-mail,
 * webhook), which signals and when (quiet hours). Bot tokens and other
 * channel secrets are server configuration and never entered here.
 */

'use client';
import type {
  AlertSubscription,
  AlertDelivery,
  AlertSubscriptionList,
  Severity,
  SignalKind,
} from '@joseph/contracts';
import {useCallback, useEffect, useState, type ReactElement} from 'react';
import type {WebFeatureContext} from '../../shell/react-bindings';
import {errorMessage} from '../trading/ui/hooks';
import styles from './alerts.module.css';
import {enableDevicePush, removeDevicePush} from './device-push';

/** Channels a user can subscribe to. */
type UserChannel = AlertSubscription['channel'];

/** Human labels and target hints per channel. */
const CHANNELS: Readonly<
  Record<UserChannel, {label: string; target: string; placeholder: string}>
> = {
  TELEGRAM: {
    label: 'Telegram',
    target: 'Chat id',
    placeholder: '123456789',
  },
  WHATSAPP: {
    label: 'WhatsApp',
    target: 'Phone number',
    placeholder: '+14155552671',
  },
  WEB_PUSH: {
    label: 'Browser / mobile push',
    target: 'Device id',
    placeholder: 'Use Enable push on this device',
  },
  LINE: {
    label: 'LINE',
    target: 'LINE user or group id',
    placeholder: 'U followed by 32 characters',
  },
  EMAIL: {label: 'E-mail', target: 'Address', placeholder: 'you@example.com'},
  WEBHOOK: {
    label: 'Webhook',
    target: 'HTTPS URL',
    placeholder: 'https://hooks.example.com/joseph',
  },
};

/** Signal kinds offered as checkboxes, in plain language. */
const KINDS: ReadonlyArray<{kind: SignalKind; label: string}> = [
  {kind: 'ENTRY_CONFIRMED', label: 'Entries'},
  {kind: 'ENTRY_BLOCKED', label: 'Blocked entries'},
  {kind: 'PRE_SIGNAL', label: 'Setups forming'},
  {kind: 'AOI_INVALIDATED', label: 'Zones invalidated'},
  {kind: 'SETUP_EXPIRED', label: 'Setups expired'},
];

/** Severities, lowest first. */
const SEVERITIES: readonly Severity[] = [
  'INFO',
  'WARNING',
  'ENTRY',
  'CRITICAL',
];

/**
 * Returns the browser's IANA time zone, or UTC.
 * @return Time zone name.
 */
function localZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
}

/**
 * Builds an empty draft subscription: Telegram, entries only, every
 * severity, all strategies, no quiet hours.
 * @return Draft.
 */
function blankDraft(): AlertSubscription {
  return {
    subscription_id: '',
    channel: 'TELEGRAM',
    target: '',
    kinds: ['ENTRY_CONFIRMED'],
    min_severity: 'INFO',
    strategy_ids: [],
    enabled: true,
    quiet_hours: null,
  };
}

/**
 * Renders the notification subscriptions panel: the saved subscriptions
 * (click one to edit it) and a form that adds or updates one.
 * @param props Feature context.
 * @return Panel.
 */
export function NotificationsPanel({
  ctx,
}: {
  ctx: WebFeatureContext;
}): ReactElement {
  const [list, setList] = useState<AlertSubscriptionList | null>(null);
  const [draft, setDraft] = useState<AlertSubscription>(blankDraft);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState(false);
  const [delivery, setDelivery] = useState<AlertDelivery | null>(null);
  const [pollUntil, setPollUntil] = useState(0);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setList(await ctx.clients.strategy.listAlertSubscriptions());
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    }
  }, [ctx]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!delivery || delivery.status !== 'PENDING' || Date.now() >= pollUntil) {
      return;
    }
    let active = true;
    const timer = setTimeout(() => {
      void ctx.clients.strategy
        .getAlertDelivery(delivery.delivery_id)
        .then(result => {
          if (active) setDelivery(result);
        })
        .catch(err => {
          if (active) setError(errorMessage(err));
        });
    }, 1000);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [ctx, delivery, pollUntil]);

  const patch = (change: Partial<AlertSubscription>) =>
    void setDraft(current => ({...current, ...change}));
  const configured = new Set(list?.configured_channels ?? []);
  const channel = CHANNELS[draft.channel];

  const save = async () => {
    setSaving(true);
    try {
      await ctx.clients.strategy.putAlertSubscription(draft);
      ctx.notifier.notify(
        `Saved notification "${draft.subscription_id}"`,
        'success'
      );
      setDraft(blankDraft());
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  const test = async (subscriptionId: string) => {
    setBusy(true);
    setError(null);
    try {
      setDelivery(
        await ctx.clients.strategy.testAlertSubscription(subscriptionId)
      );
      setPollUntil(Date.now() + 60_000);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (sub: AlertSubscription) => {
    setBusy(true);
    try {
      await ctx.clients.strategy.deleteAlertSubscription(sub.subscription_id);
      if (sub.channel === 'WEB_PUSH') {
        try {
          await removeDevicePush(sub.target);
        } catch {
          setMessage(
            'Destination removed from the server. If this browser still shows a push subscription, disable notifications in its settings.'
          );
        }
      }
      if (draft.subscription_id === sub.subscription_id) setDraft(blankDraft());
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const enablePush = async () => {
    setBusy(true);
    setError(null);
    try {
      const saved = await enableDevicePush(
        ctx.clients.strategy,
        list?.web_push_public_key ?? ''
      );
      setDraft(saved);
      setMessage(
        'Push enabled on this device. Use Send test to check delivery.'
      );
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={styles.panel} data-testid="notifications-panel">
      {error && (
        <div className="j-notice j-notice--danger" role="alert">
          {error}
        </div>
      )}
      <div className={styles.scroll}>
        <section
          className={styles.deviceCard}
          aria-label="Browser and mobile push"
        >
          <strong>Receive alerts on this device</strong>
          <p>
            Enable background notifications, then send a test. On iPhone or
            iPad, add Joseph to your Home Screen and open it there. HTTPS and
            notification permission are required.
          </p>
          <button
            type="button"
            className="j-btn"
            disabled={busy || !list?.web_push_public_key}
            onClick={() => void enablePush()}
          >
            Enable push on this device
          </button>
          {list && !list.web_push_public_key && (
            <p className="j-field__hint">
              An operator must configure Web Push keys on the market engine
              first. See README → Alert delivery.
            </p>
          )}
          {message && <p role="status">{message}</p>}
        </section>
        {delivery && (
          <p className="j-notice" role="status">
            {delivery.status === 'DELIVERED'
              ? 'Test accepted by the provider. Check the receiving device; acceptance is not a read receipt.'
              : delivery.status === 'DEAD'
                ? `Test failed: ${delivery.last_error ?? 'Delivery rejected'}`
                : `Test queued (${delivery.attempts} attempts). Delivery may retry; check this destination before sending another test.`}
          </p>
        )}
        <table className="j-table" aria-label="Notification subscriptions">
          <thead>
            <tr>
              <th>Name</th>
              <th>Channel</th>
              <th>Target</th>
              <th>Signals</th>
              <th>Quiet hours</th>
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {(list?.subscriptions ?? []).map(sub => (
              <tr
                key={sub.subscription_id}
                className={sub.enabled ? undefined : 'j-row--muted'}
              >
                <td>
                  <button
                    type="button"
                    className="j-btn j-btn--ghost"
                    aria-label={`Edit ${sub.subscription_id}`}
                    onClick={() => void setDraft(structuredClone(sub))}
                  >
                    {sub.subscription_id}
                  </button>
                </td>
                <td>{CHANNELS[sub.channel].label}</td>
                <td>{sub.target}</td>
                <td>
                  {sub.kinds.length
                    ? sub.kinds
                        .map(k => KINDS.find(x => x.kind === k)?.label ?? k)
                        .join(', ')
                    : 'All'}
                </td>
                <td>
                  {sub.quiet_hours
                    ? `${sub.quiet_hours.start}–${sub.quiet_hours.end} ${sub.quiet_hours.tz}`
                    : 'None'}
                </td>
                <td>
                  {!configured.has(sub.channel) ? (
                    <span className="j-badge j-badge--sodium">
                      Not delivered
                    </span>
                  ) : sub.enabled ? (
                    <span className="j-badge j-badge--live">On</span>
                  ) : (
                    <span className="j-badge j-badge--muted">Off</span>
                  )}
                </td>
                <td>
                  <div className={styles.actions}>
                    <button
                      type="button"
                      className="j-btn"
                      disabled={
                        busy || !sub.enabled || !configured.has(sub.channel)
                      }
                      onClick={() => void test(sub.subscription_id)}
                      aria-label={`Send test to ${sub.subscription_id}`}
                    >
                      Send test
                    </button>
                    <button
                      type="button"
                      className="j-btn j-btn--ghost"
                      disabled={busy}
                      onClick={() => void remove(sub)}
                      aria-label={`Delete ${sub.subscription_id}`}
                    >
                      Delete
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {list && list.subscriptions.length === 0 && (
          <div className="j-empty">
            <strong>No notifications yet</strong>
            <p>
              Signals always appear in Alerts. Add a channel below to also get
              them on your phone or another system.
            </p>
          </div>
        )}
        <form
          className={styles.notifyForm}
          aria-label="Notification settings"
          onSubmit={event => {
            event.preventDefault();
            void save();
          }}
        >
          <label className="j-field">
            <span className="j-label">Name</span>
            <input
              className="j-input"
              value={draft.subscription_id}
              placeholder="phone"
              onChange={e =>
                void patch({subscription_id: e.target.value.toLowerCase()})
              }
              required
            />
          </label>
          <label className="j-field">
            <span className="j-label">Channel</span>
            <select
              className="j-select"
              value={draft.channel}
              onChange={e =>
                void patch({channel: e.target.value as UserChannel, target: ''})
              }
            >
              {(Object.keys(CHANNELS) as UserChannel[]).map(key => (
                <option key={key} value={key}>
                  {CHANNELS[key].label}
                </option>
              ))}
            </select>
          </label>
          <label className="j-field">
            <span className="j-label">{channel.target}</span>
            <input
              className="j-input"
              value={draft.target}
              placeholder={channel.placeholder}
              onChange={e => void patch({target: e.target.value.trim()})}
              required
              readOnly={draft.channel === 'WEB_PUSH'}
            />
          </label>
          {(draft.channel === 'WHATSAPP' || draft.channel === 'TELEGRAM') && (
            <p className="j-field__hint">
              {draft.channel === 'WHATSAPP'
                ? 'Use an opted-in international phone number. The server needs a Meta Business token, phone number id and approved template. Meta charges may apply.'
                : 'Start a chat with your Telegram bot first. Enter your numeric chat id; the bot token stays on the server.'}{' '}
              See README → Alert delivery. Strategy alerts must also allow this
              channel.
            </p>
          )}
          <label className="j-field">
            <span className="j-label">Lowest severity</span>
            <select
              className="j-select"
              value={draft.min_severity}
              onChange={e =>
                void patch({min_severity: e.target.value as Severity})
              }
            >
              {SEVERITIES.map(level => (
                <option key={level} value={level}>
                  {level.charAt(0) + level.slice(1).toLowerCase()}
                </option>
              ))}
            </select>
          </label>
          <fieldset className={styles.notifyKinds}>
            <legend className="j-label">Signals to send</legend>
            {KINDS.map(({kind, label}) => (
              <label key={kind} className="j-check">
                <input
                  type="checkbox"
                  checked={draft.kinds.includes(kind)}
                  onChange={e =>
                    void patch({
                      kinds: e.target.checked
                        ? [...draft.kinds, kind]
                        : draft.kinds.filter(k => k !== kind),
                    })
                  }
                />
                {label}
              </label>
            ))}
          </fieldset>
          <label className="j-check">
            <input
              type="checkbox"
              checked={draft.quiet_hours !== null}
              onChange={e =>
                void patch({
                  quiet_hours: e.target.checked
                    ? {
                        start: '23:00',
                        end: '07:00',
                        tz: localZone(),
                        bypass_severities: ['CRITICAL'],
                      }
                    : null,
                })
              }
            />
            Hold notifications during quiet hours (critical ones still arrive)
          </label>
          {draft.quiet_hours && (
            <div className={styles.inline}>
              <label className="j-field">
                <span className="j-label">From</span>
                <input
                  className="j-input"
                  type="time"
                  value={draft.quiet_hours.start}
                  onChange={e =>
                    draft.quiet_hours &&
                    void patch({
                      quiet_hours: {
                        ...draft.quiet_hours,
                        start: e.target.value,
                      },
                    })
                  }
                />
              </label>
              <label className="j-field">
                <span className="j-label">Until</span>
                <input
                  className="j-input"
                  type="time"
                  value={draft.quiet_hours.end}
                  onChange={e =>
                    draft.quiet_hours &&
                    void patch({
                      quiet_hours: {...draft.quiet_hours, end: e.target.value},
                    })
                  }
                />
              </label>
              <span className="j-field__hint">{draft.quiet_hours.tz}</span>
            </div>
          )}
          <label className="j-check">
            <input
              type="checkbox"
              checked={draft.enabled}
              onChange={e => void patch({enabled: e.target.checked})}
            />
            Enabled
          </label>
          {list && !configured.has(draft.channel) && (
            <p className="j-notice j-notice--warning" role="status">
              The server has no {channel.label} credentials, so these
              notifications are saved but not delivered until an operator
              configures the channel.
            </p>
          )}
          <div className={styles.actions}>
            <button
              type="submit"
              className="j-btn j-btn--primary"
              disabled={saving}
            >
              {saving ? 'Saving…' : 'Save notification'}
            </button>
            <button
              type="button"
              className="j-btn j-btn--ghost"
              onClick={() => void setDraft(blankDraft())}
            >
              Clear form
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
