import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  censusApi: vi.fn(), dispatchBrowserLabel: vi.fn(),
}));
vi.mock('next/router', () => ({ useRouter: () => ({ isReady: true, query: {} }) }));
vi.mock('../components/layout/AppShell', () => ({ default: ({ children }) => <div>{children}</div> }));
vi.mock('../lib/inventoryPrintTransport', () => ({ dispatchBrowserLabel: mocks.dispatchBrowserLabel }));
vi.mock('../lib/api', () => ({ censusApi: mocks.censusApi }));
import ShelfCensusPage from '../pages/inventory/census';

describe('Shelf Census flow', () => {
  it('keeps location, confirms an existing record, queues its label, and returns focus', async () => {
    const location = { location_key: '01175/A/01', unit_id: '01175', section_code: 'A', shelf_level: '01', position_code: null, location_type: 'shelf', accessibility: 'accessible', is_full: false };
    mocks.censusApi.mockImplementation(async (path, options = {}) => {
      if (path === '/locations' && options.method === 'POST') return location;
      if (path === '/locations') return { items: [location] };
      if (path.startsWith('/sessions/')) return { session_key: 'session-test', active_location_key: location.location_key, items_processed: 0, matched: 0, unmatched: 0, labels_pending: 0, labels_submitted: 0 };
      if (path === '/label-template') return { template: { version: 2, coordinate_unit: 'mm', dpi: 203, print_scale_percent: 100 } };
      if (path === '/print-settings') return { mode: 'browser', layout: 'qr', agents: [], automatic_ready: false };
      if (path === '/print-jobs?limit=20') return { items: [] };
      if (path === '/search?query=blue%20lamp') return { items: [{ listing_id: 23, title: 'Blue Lamp', sku: 'KEEP-SKU', locations: [], status: 'draft' }] };
      if (path === '/confirm') return { status: 'created', replayed: false, print_mode: 'browser', item_uuid: 'a1b2c3d4-1111-4222-8333-abcdefabcdef', title: 'Blue Lamp', sku: 'KEEP-SKU', location_key: location.location_key, print_job_id: 5 };
      if (path.includes('/label')) return { image_data_url: 'data:image/png;base64,AA==', print_scale_percent: 100 };
      return {};
    });
    render(<ShelfCensusPage />);
    const input = await screen.findByLabelText('Dictate or scan product identification');
    fireEvent.change(screen.getByLabelText('Storage unit'), { target: { value: '01175' } });
    fireEvent.change(screen.getByLabelText('Shelving section'), { target: { value: 'A' } });
    fireEvent.change(screen.getByLabelText('Shelf level'), { target: { value: '01' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save / change location' }));
    await waitFor(() => expect(mocks.censusApi).toHaveBeenCalledWith('/locations', expect.objectContaining({ method: 'POST' })));
    fireEvent.change(input, { target: { value: 'blue lamp' } });
    fireEvent.click(screen.getByRole('button', { name: 'Search catalog' }));
    fireEvent.click(await screen.findByRole('option', { name: /Blue Lamp/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm selected product' }));
    await waitFor(() => expect(mocks.censusApi).toHaveBeenCalledWith('/confirm', expect.objectContaining({ method: 'POST', body: expect.stringContaining('01175/A/01') })));
    await waitFor(() => expect(mocks.dispatchBrowserLabel).toHaveBeenCalled());
    expect(input).toHaveFocus();
    expect(screen.getByText(/Active: 01175 \/ A \/ 01/)).toBeInTheDocument();
    expect(mocks.censusApi).toHaveBeenCalledWith('/print-jobs/5/browser-dispatch', expect.objectContaining({ method: 'POST' }));
  });
});
