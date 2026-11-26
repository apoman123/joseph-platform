/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Component tests for the notification subscriptions panel.
 */

import {DemoStrategyClient} from '@joseph/mock-provider';
import {render, screen, waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {describe, expect, it, vi} from 'vitest';
import type {WebFeatureContext} from '../../shell/react-bindings';
import {NotificationsPanel} from './notifications-panel';
import * as devicePush from './device-push';

/**
 * Builds a minimal context with a DEMO strategy client.
 * @param client Strategy client.
 * @return Context and notifier spy.
 */
function makeContext(client: DemoStrategyClient): {
  ctx: WebFeatureContext;
  notify: ReturnType<typeof vi.fn>;
} {
  const notify = vi.fn();
  // A deliberate partial context: the panel uses only these two members.
  const ctx = {
    clients: {strategy: client},
    notifier: {notify},
  } as unknown as WebFeatureContext;
  return {ctx, notify};
}

describe('NotificationsPanel', () => {
  it('saves a Telegram subscription and warns when the channel is unconfigured', async () => {
    const client = new DemoStrategyClient();
    const {ctx, notify} = makeContext(client);
    const user = userEvent.setup();
    render(<NotificationsPanel ctx={ctx} />);
    await screen.findByText('No notifications yet');
    expect(screen.getByText(/no Telegram credentials/i)).toBeInTheDocument();
    await user.type(screen.getByLabelText('Name'), 'phone');
    await user.type(screen.getByLabelText('Chat id'), '123456789');
    await user.click(screen.getByLabelText(/quiet hours/));
    await user.click(screen.getByRole('button', {name: 'Save notification'}));
    await waitFor(
      () =>
        void expect(notify).toHaveBeenCalledWith(
          'Saved notification "phone"',
          'success'
        )
    );
    const row = await screen.findByRole('row', {name: /phone/});
    expect(row).toHaveTextContent('Telegram');
    expect(row).toHaveTextContent('Not delivered');
    const stored = await client.listAlertSubscriptions();
    expect(stored.subscriptions[0]?.quiet_hours?.start).toBe('23:00');
  });

  it('changes the target label per channel', async () => {
    const {ctx} = makeContext(new DemoStrategyClient());
    const user = userEvent.setup();
    render(<NotificationsPanel ctx={ctx} />);
    await user.selectOptions(screen.getByLabelText('Channel'), 'WEBHOOK');
    expect(screen.getByLabelText('HTTPS URL')).toBeInTheDocument();
  });

  it('queues a test, polls provider acceptance, then deletes the saved destination', async () => {
    const client = new DemoStrategyClient();
    await client.putAlertSubscription({
      subscription_id: 'phone',
      channel: 'TELEGRAM',
      target: '123',
      enabled: true,
      kinds: [],
      min_severity: 'INFO',
      strategy_ids: [],
      quiet_hours: null,
    });
    const originalList = client.listAlertSubscriptions.bind(client);
    vi.spyOn(client, 'listAlertSubscriptions').mockImplementation(async () => ({
      ...(await originalList()),
      configured_channels: ['TELEGRAM'],
    }));
    const pending = {
      delivery_id: 'test-1',
      channel: 'TELEGRAM',
      status: 'PENDING' as const,
      attempts: 0,
      last_error: null,
    };
    const send = vi
      .spyOn(client, 'testAlertSubscription')
      .mockResolvedValue(pending);
    const progress = vi
      .spyOn(client, 'getAlertDelivery')
      .mockResolvedValue({...pending, status: 'DELIVERED', attempts: 1});
    const {ctx} = makeContext(client);
    const user = userEvent.setup();
    render(<NotificationsPanel ctx={ctx} />);
    await user.click(
      await screen.findByRole('button', {name: 'Send test to phone'})
    );
    expect(send).toHaveBeenCalledWith('phone');
    expect(
      await screen.findByText(
        /Test accepted by the provider/,
        {},
        {timeout: 3000}
      )
    ).toBeInTheDocument();
    expect(progress).toHaveBeenCalledWith('test-1');
    await user.click(screen.getByRole('button', {name: 'Delete phone'}));
    await waitFor(
      () =>
        void expect(
          screen.queryByRole('button', {name: 'Edit phone'})
        ).not.toBeInTheDocument()
    );
  });

  it('provides WhatsApp setup hints and reports missing credentials', async () => {
    const {ctx} = makeContext(new DemoStrategyClient());
    const user = userEvent.setup();
    render(<NotificationsPanel ctx={ctx} />);
    await user.selectOptions(
      screen.getByRole('combobox', {name: 'Channel'}),
      'WHATSAPP'
    );
    expect(screen.getByLabelText('Phone number')).toBeInTheDocument();
    expect(screen.getByText(/Meta Business token/)).toBeInTheDocument();
    expect(screen.getByText(/no WhatsApp credentials/)).toBeInTheDocument();
  });

  it('removes the server row even if local device unsubscribe fails', async () => {
    const client = new DemoStrategyClient();
    await client.putAlertSubscription({
      subscription_id: 'device',
      channel: 'WEB_PUSH',
      target: 'device-test',
      enabled: true,
      kinds: [],
      min_severity: 'INFO',
      strategy_ids: [],
      quiet_hours: null,
    });
    const unsubscribe = vi
      .spyOn(devicePush, 'removeDevicePush')
      .mockRejectedValue(new Error('browser offline'));
    const {ctx} = makeContext(client);
    const user = userEvent.setup();
    render(<NotificationsPanel ctx={ctx} />);
    await user.click(
      await screen.findByRole('button', {name: 'Delete device'})
    );
    await waitFor(() => {
      expect(
        screen.queryByRole('button', {name: 'Edit device'})
      ).not.toBeInTheDocument();
    });
    expect(
      await screen.findByText(/Destination removed from the server/)
    ).toBeInTheDocument();
    unsubscribe.mockRestore();
  });
});
