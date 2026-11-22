/**
 * @fileoverview Component tests for the notification subscriptions panel.
 */
import {DemoStrategyClient} from '@joseph/mock-provider';
import {render, screen, waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {describe, expect, it, vi} from 'vitest';
import type {WebFeatureContext} from '../../shell/react-bindings';
import {NotificationsPanel} from './notifications-panel';

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
    await waitFor(() =>
      expect(notify).toHaveBeenCalledWith(
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
});
