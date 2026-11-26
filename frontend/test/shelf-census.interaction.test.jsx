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
import InventoryPickingPage from '../pages/inventory/picking';
import { getServerSideProps as resolveInventoryShortLink } from '../pages/i/[token]';

describe('inventory identity links', () => {
  it('redirects a valid permanent token to the authenticated census lookup', async () => {
    await expect(resolveInventoryShortLink({ params: { token: 'abcdefghjklmn' } })).resolves.toEqual({
      redirect: { destination: '/inventory/census?item=ABCDEFGHJKLMN', permanent: false },
    });
    await expect(resolveInventoryShortLink({ params: { token: 'not-a-token' } })).resolves.toEqual({
      redirect: { destination: '/inventory/census', permanent: false },
    });
  });
});

describe('Shelf Census flow', () => {
  it('keeps location, confirms an existing record, queues its label, and returns focus', async () => {
    const location = { location_key: '01175/A/01', unit_id: '01175', section_code: 'A', shelf_level: '01', position_code: null, location_type: 'shelf', accessibility: 'accessible', is_full: false };
    mocks.censusApi.mockImplementation(async (path, options = {}) => {
      if (path === '/locations' && options.method === 'POST') return location;
      if (path === '/locations') return { items: [location] };
      if (path.startsWith('/sessions/')) return { session_key: 'session-test', active_location_key: location.location_key, items_processed: 0, matched: 0, unmatched: 0, labels_pending: 0, labels_submitted: 0 };
      if (path === '/label-template') return { template: { version: 2, coordinate_unit: 'mm', dpi: 203, print_scale_percent: 100 } };
      if (path === '/print-settings') return { mode: 'browser', layout: 'qr', copies: 2, agents: [], automatic_ready: false };
      if (path === '/print-jobs?limit=20') return { items: [] };
      if (path === '/search?query=blue%20lamp') return { items: [{ listing_id: 23, title: 'Blue Lamp', sku: 'KEEP-SKU', locations: [], status: 'draft' }] };
      if (path === '/confirm') return { status: 'created', replayed: false, print_mode: 'browser', copies: 2, item_uuid: 'a1b2c3d4-1111-4222-8333-abcdefabcdef', title: 'Blue Lamp', sku: 'KEEP-SKU', location_key: location.location_key, print_job_id: 5 };
      if (path.includes('/label')) return { image_data_url: 'data:image/png;base64,AA==', print_scale_percent: 100 };
      return {};
    });
    render(<ShelfCensusPage />);
    const input = await screen.findByLabelText('Dictate or scan product identification');
    fireEvent.change(screen.getByLabelText('Storage unit'), { target: { value: '01175' } });
    fireEvent.change(screen.getByLabelText('Shelving section'), { target: { value: 'A' } });
    fireEvent.change(screen.getByLabelText('Shelf level'), { target: { value: '01' } });
    fireEvent.click(screen.getByRole('button', { name: 'Use Same Location' }));
    await waitFor(() => expect(mocks.censusApi).toHaveBeenCalledWith('/locations', expect.objectContaining({ method: 'POST' })));
    fireEvent.change(input, { target: { value: 'blue lamp' } });
    fireEvent.click(screen.getByRole('button', { name: 'Search catalog' }));
    fireEvent.click(await screen.findByRole('option', { name: /Blue Lamp/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm selected product' }));
    await waitFor(() => expect(mocks.censusApi).toHaveBeenCalledWith('/confirm', expect.objectContaining({ method: 'POST', body: expect.stringContaining('01175/A/01') })));
    await screen.findByRole('img', { name: 'Inventory label preview at 58 by 40 millimeters' });
    expect(mocks.dispatchBrowserLabel).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Print SKU Label (2 copies)' }));
    await waitFor(() => expect(mocks.dispatchBrowserLabel).toHaveBeenCalledWith('data:image/png;base64,AA==', expect.objectContaining({ copies: 2, scalePercent: 100 })));
    expect(input).toHaveFocus();
    expect(screen.getByText(/Active: 01175 \/ A \/ 01/)).toBeInTheDocument();
    expect(mocks.censusApi).toHaveBeenCalledWith('/print-jobs/5/browser-dispatch', expect.objectContaining({ method: 'POST' }));
  });

  it('scans and verifies a sold item through pick, pack, label, and handoff without publishing', async () => {
    let state = 'AWAITING_PICK';
    const order = { sale_id: 41, order_id: 'ORDER-41', marketplace: 'ebay', product: 'Blue lantern', sku: 'LANTERN-1', quantity: 1, deadline: null, pick_state: state, locations: [{ item_uuid: 'unit-uuid', barcode_token: 'ABCDEFGHIJKLM', sku: 'LANTERN-1', location: '01175/A01', location_key: '01175/A/01' }], picked_item_uuids: [], location_unknown: false };
    mocks.censusApi.mockImplementation(async (path, options = {}) => {
      if (path === '/picking/queue?limit=250') return { items: state === 'SHIPPED' ? [] : [{ ...order, pick_state: state, picked_item_uuids: state === 'AWAITING_PICK' ? [] : ['unit-uuid'] }], count: state === 'SHIPPED' ? 0 : 1 };
      if (path.startsWith('/picking/sales/41/state') && options.method === 'POST') { state = JSON.parse(options.body).state; return { status: state, marketplace_fulfillment_updated: false }; }
      return {};
    });
    render(<InventoryPickingPage />);
    const scanInput = await screen.findByPlaceholderText(/Scan the permanent item label/);
    fireEvent.change(scanInput, { target: { value: 'ABCDEFGHIJKLM' } });
    fireEvent.keyDown(scanInput, { key: 'Enter' });
    fireEvent.click(await screen.findByRole('button', { name: 'Confirm Picked' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Confirm Packed' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Mark Label Ready' }));
    fireEvent.click(await screen.findByRole('checkbox', { name: /handed this package to the carrier/i }));
    fireEvent.click(await screen.findByRole('button', { name: 'Mark Shipped' }));
    await screen.findByText('No matched, unshipped sales are waiting in the pick queue.');
    expect(mocks.censusApi).toHaveBeenCalledWith('/picking/sales/41/state', expect.objectContaining({ method: 'POST', body: expect.stringContaining('ABCDEFGHIJKLM') }));
    expect(state).toBe('SHIPPED');
  });
});
