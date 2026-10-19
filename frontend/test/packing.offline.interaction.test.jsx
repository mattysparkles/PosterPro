import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const api = vi.hoisted(() => ({
  attachIntakeSlatePhoto: vi.fn(), attachIntakeSlateVoiceNote: vi.fn(), createIntakeSlate: vi.fn(),
  dispatchScannerScan: vi.fn(), fetchScannerCommands: vi.fn(async () => ({ commands: [] })),
  fetchScannerSession: vi.fn(), findInventoryItem: vi.fn(), moveInventoryStock: vi.fn(),
}));
vi.mock('../components/layout/AppShell', () => ({ default: ({ children }) => <main>{children}</main> }));
vi.mock('../lib/api', () => api);

import PackingWorkstation from '../pages/packing';
import { __offlineJournal, pendingIntakeEvents } from '../lib/intakeOfflineJournal';
const originalOnline = window.navigator.onLine;

afterEach(() => {
  __offlineJournal.memoryStores.events.clear();
  __offlineJournal.memoryStores.state.clear();
  localStorage.clear();
  Object.defineProperty(window.navigator, 'onLine', { configurable: true, value: originalOnline });
  vi.restoreAllMocks();
});

describe('packing workstation offline journal', () => {
  it('retains the scan locally without classifying a barcode or creating a Slate offline', async () => {
    Object.defineProperty(window.navigator, 'onLine', { configurable: true, value: false });
    render(<PackingWorkstation />);

    const input = screen.getByPlaceholderText('Scan or type barcode');
    fireEvent.change(input, { target: { value: '012345678905' } });
    fireEvent.submit(input.closest('form'));

    expect(await screen.findByText('Unidentified barcode')).toBeTruthy();
    expect(await screen.findByText(/waiting to sync/)).toBeTruthy();
    await waitFor(async () => expect((await pendingIntakeEvents()).map((event) => event.type)).toEqual(['packing_scan']));
    const photo = new File(['offline-photo-bytes'], 'part-label.jpg', { type: 'image/jpeg' });
    fireEvent.change(document.querySelector('input[type="file"]'), { target: { files: [photo] } });
    await waitFor(async () => expect((await pendingIntakeEvents()).map((event) => event.type)).toEqual(['packing_scan', 'packing_photo']));
    expect(api.dispatchScannerScan).not.toHaveBeenCalled();
    expect(api.createIntakeSlate).not.toHaveBeenCalled();
    expect(api.attachIntakeSlatePhoto).not.toHaveBeenCalled();
  });

  it('recognizes plain-text scanner commands offline instead of treating them as product barcodes', async () => {
    Object.defineProperty(window.navigator, 'onLine', { configurable: true, value: false });
    render(<PackingWorkstation />);
    const input = screen.getByPlaceholderText('Scan or type barcode');

    fireEvent.change(input, { target: { value: '012345678905' } });
    fireEvent.submit(input.closest('form'));
    expect(await screen.findByText('Unidentified barcode')).toBeTruthy();

    fireEvent.change(input, { target: { value: '  NEXT   ITEM ' } });
    fireEvent.submit(input.closest('form'));
    expect(await screen.findByText(/Scan NEW ITEM then a product barcode/)).toBeTruthy();
    const events = await pendingIntakeEvents();
    expect(events.map((event) => event.payload.scan)).toEqual(['012345678905', 'NEXT   ITEM']);
    expect(api.createIntakeSlate).not.toHaveBeenCalled();
  });

  it('keeps a tote code scan out of product capture after NEW TOTE while offline', async () => {
    Object.defineProperty(window.navigator, 'onLine', { configurable: true, value: false });
    render(<PackingWorkstation />);
    const input = screen.getByPlaceholderText('Scan or type barcode');
    fireEvent.change(input, { target: { value: 'NEW TOTE' } });
    fireEvent.submit(input.closest('form'));
    expect(await screen.findByRole('status')).toHaveTextContent('Scan or enter the new tote code next');
    fireEvent.change(input, { target: { value: 'AO-TOTE-42' } });
    fireEvent.submit(input.closest('form'));
    await waitFor(() => expect(screen.getByPlaceholderText('AO9').value).toBe('AO-TOTE-42'));
    expect(screen.queryByText('Unidentified barcode')).toBeNull();
    expect(api.createIntakeSlate).not.toHaveBeenCalled();
  });

  it('queues FIND ITEM scan codes as lookup queries offline, not product captures', async () => {
    Object.defineProperty(window.navigator, 'onLine', { configurable: true, value: false });
    render(<PackingWorkstation />);
    const input = screen.getByPlaceholderText('Scan or type barcode');
    fireEvent.change(input, { target: { value: 'FIND ITEM' } });
    fireEvent.submit(input.closest('form'));
    expect(await screen.findByRole('status')).toHaveTextContent("Scan the item's barcode");
    fireEvent.change(input, { target: { value: '8901234567890' } });
    fireEvent.submit(input.closest('form'));
    await waitFor(() => expect(screen.queryByText('Unidentified barcode')).toBeNull());
    expect(api.createIntakeSlate).not.toHaveBeenCalled();
    expect((await pendingIntakeEvents()).map((event) => event.payload.scan)).toEqual(['FIND ITEM', '8901234567890']);
  });

  it('requires explicit confirmation before moving a uniquely identified scanned item', async () => {
    Object.defineProperty(window.navigator, 'onLine', { configurable: true, value: true });
    const productLookup = { found: true, match_type: 'product', products: [{ product_id: 7, title: 'Lens cap', barcode: 'MOVE-1', locations: [{ location_key: 'source-id', label: 'AO-SOURCE', on_hand: 3, available: 3, move_eligible: true }] }] };
    api.dispatchScannerScan.mockImplementation(async (value) => {
      if (String(value).includes('MOVE_ITEM')) return { command: 'move_item', workstation_state: { awaiting_move_query: true } };
      return { kind: 'inventory_move_selection', lookup: productLookup, workstation_state: { last_inventory_lookup: { ...productLookup, move_mode: true }, awaiting_move_query: false } };
    });
    api.findInventoryItem.mockResolvedValueOnce({ match_type: 'package', package: { package_uuid: 'destination-id', status: 'open' } }).mockResolvedValueOnce(productLookup);
    api.moveInventoryStock.mockResolvedValue({ status: 'applied', quantity: 2 });
    localStorage.setItem('posterpro.packing.session.v1', JSON.stringify({ id: 'move-ui-session', tote: 'AO-DEST', current_package_code: 'PKG-DEST', quantity: 1, current: null, history: [] }));
    render(<PackingWorkstation />);
    fireEvent.change(screen.getByPlaceholderText('AO9'), { target: { value: 'AO-DEST' } });
    fireEvent.click(screen.getAllByText('Move item')[0]);
    const input = screen.getByPlaceholderText('Scan or type barcode');
    fireEvent.change(input, { target: { value: 'MOVE-1' } });
    fireEvent.submit(input.closest('form'));
    expect(await screen.findByText('Lens cap · MOVE-1')).toBeTruthy();
    expect(screen.getByLabelText('Move destination type').value).toBe('package');
    expect(api.moveInventoryStock).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('Move quantity'), { target: { value: '2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm move' }));
    await waitFor(() => expect(api.moveInventoryStock).toHaveBeenCalledWith({ product_id: 7, quantity: 2, source_location_key: 'source-id', destination_location_key: 'destination-id' }, expect.any(String)));
    expect(api.createIntakeSlate).not.toHaveBeenCalled();
  });

  it('captures an existing product in a review-only bundle without creating a Slate', async () => {
    Object.defineProperty(window.navigator, 'onLine', { configurable: true, value: true });
    api.createIntakeSlate.mockClear();
    api.moveInventoryStock.mockClear();
    const draft = { bundle_id: 'BND-TEST', status: 'collecting', components: [], stock_reserved: false, listing_created: false };
    api.dispatchScannerScan.mockImplementation(async (value) => {
      const scan = String(value).replace(/^POSTERPRO:COMMAND:/i, '').replaceAll('_', ' ').toUpperCase();
      if (scan === 'BUNDLE') return { command: 'start_bundle', bundle_draft: draft, workstation_state: { bundle_draft: draft } };
      if (scan === 'ADD TO BUNDLE') return { command: 'add_to_bundle', workstation_state: { ...draft, awaiting_bundle_scan: true, bundle_draft: draft } };
      if (scan === 'BUNDLE-123') {
        const component = { product_id: 9, product_key: 'pairing-receiver', title: 'Pairing Receiver', barcode: 'BUNDLE-123', quantity: 2, available_at_capture: 2 };
        const finishedDraft = { ...draft, components: [component] };
        return { kind: 'bundle_component_selection', bundle_component: component, bundle_draft: finishedDraft, workstation_state: { bundle_draft: finishedDraft } };
      }
      if (scan === 'FINISH BUNDLE') return { command: 'finish_bundle', bundle_draft: { ...draft, status: 'review_required' }, warning: 'Bundle capture saved for review only. It is not a sellable product, stock reservation, or marketplace listing.', workstation_state: { bundle_draft: { ...draft, status: 'review_required' } } };
      return { kind: 'command', command: 'unknown' };
    });
    render(<PackingWorkstation />);

    fireEvent.click(screen.getAllByRole('button', { name: 'Start bundle' })[0]);
    expect(await screen.findByText(/Bundle capture · BND-TEST/)).toBeTruthy();
    fireEvent.click(screen.getAllByRole('button', { name: 'Add scanned item' })[0]);
    expect(await screen.findByText(/Scan an existing product barcode to add it to the active bundle capture/)).toBeTruthy();
    const input = screen.getByPlaceholderText('Scan or type barcode');
    fireEvent.change(input, { target: { value: 'BUNDLE-123' } });
    fireEvent.submit(input.closest('form'));
    expect(await screen.findByText(/Pairing Receiver/)).toBeTruthy();
    fireEvent.click(screen.getAllByRole('button', { name: 'Finish bundle' })[0]);
    expect(await screen.findByText('Status: review_required · no stock reserved · not a listing')).toBeTruthy();
    expect(screen.getByText('Review-only grouping')).toBeTruthy();
    expect(api.createIntakeSlate).not.toHaveBeenCalled();
    expect(api.moveInventoryStock).not.toHaveBeenCalled();
  });
});
