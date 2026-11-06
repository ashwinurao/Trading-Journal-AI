import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import EdgeTraderAuto from './EdgeTraderAuto';
import { importApi } from '../api';

jest.mock('../api', () => ({ importApi: { edgeTraderStatus: jest.fn(), edgeTraderPull: jest.fn() } }));

const ACCOUNTS = [{ id: 1, name: 'Day Trading' }];
const ON = {
  enabled: true, state: 'ok', poll_s: 60, account_id: 1,
  last_pull_at: '2026-11-05T15:05:12+00:00', last_ok_at: '2026-11-05T15:05:12+00:00',
  message: '2 new fill(s), 1 trade group(s) written.',
};

test('off: says how to turn it on and offers no button', async () => {
  importApi.edgeTraderStatus.mockResolvedValue({ data: { enabled: false, state: 'off', message: 'Automatic import is off. Set EDGE_TRADER_URL.' } });
  render(<EdgeTraderAuto accounts={ACCOUNTS} />);
  expect(await screen.findByText(/Automatic import is off/)).toBeInTheDocument();
  expect(screen.queryByRole('button')).toBeNull();
});

test('on: shows the account, the last pull in New York time and the result', async () => {
  importApi.edgeTraderStatus.mockResolvedValue({ data: ON });
  render(<EdgeTraderAuto accounts={ACCOUNTS} />);
  expect(await screen.findByText(/every 60 s into Day Trading/)).toBeInTheDocument();
  // 15:05:12 UTC on 2026-11-05 is 10:05:12 EST
  expect(screen.getByText(/Last pull 10:05:12 ET: 2 new fill/)).toBeInTheDocument();
});

test('waiting: the reason is shown as a caution with the last good pull, and Pull now asks again', async () => {
  importApi.edgeTraderStatus.mockResolvedValue({ data: { ...ON, state: 'waiting', message: 'Edge Trader is still loading its fill history from Schwab. Nothing was imported.' } });
  importApi.edgeTraderPull.mockResolvedValue({ data: ON });
  render(<EdgeTraderAuto accounts={ACCOUNTS} />);
  const box = await screen.findByTestId('edge-auto');
  expect(box.className).toContain('caution');
  expect(screen.getByText(/Last good pull 10:05:12 ET/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /Pull now/ }));
  await waitFor(() => expect(importApi.edgeTraderPull).toHaveBeenCalled());
  await waitFor(() => expect(screen.getByTestId('edge-auto').className).toContain('pos'));
});
