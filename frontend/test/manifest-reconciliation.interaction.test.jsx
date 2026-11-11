import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  fetchInventoryTotes: vi.fn(async () => ({ totes: [{ code: 'AO1', container_uuid: 'container-1', unit_count: 3 }] })),
  fetchManifestHistory: vi.fn(async () => ({ imports: [] })),
  createInventoryTote: vi.fn(), fetchManifestImport: vi.fn(), importManifest: vi.fn(), previewManifestImport: vi.fn(), reconcileManifestEntry: vi.fn(),
}));
vi.mock('next/link', () => ({ default: ({ children, ...props }) => <a {...props}>{children}</a> }));
vi.mock('../components/layout/AppShell', () => ({ default: ({ children }) => <main>{children}</main> }));
vi.mock('../lib/api', () => api);
import ManifestImportPage from '../pages/inventory/manifests';

describe('manifest reconciliation workflow', () => {
  it('previews a manifest and reconciles an existing-location conflict by moving stock', async () => {
    api.previewManifestImport.mockResolvedValue({ raw_scan_count: 2, unique_barcode_count: 1, duplicate_policy: 'quantity', duplicate_occurrences: [{ barcode: '123', extra_scans: 1 }], existing_location_conflicts: [{ barcode: '123', source_locations: [{ label: 'AO9', available: 2 }] }], proposed_changes: [{ barcode: '123', quantity: 2 }], prior_imports: [] });
    api.importManifest.mockResolvedValue({ status: 'pending_reconciliation', import_id: 22 });
    api.fetchManifestImport.mockResolvedValue({ id: 22, tote_code: 'AO1', status: 'pending_reconciliation', duplicate_policy: 'quantity', entries: [{ id: 7, barcode: '123', product_title: 'Scanner', scan_count: 2, applied_quantity: 0, resolution: 'pending_reconciliation', source_locations: [{ location_key: 'container-9', label: 'AO9', available: 2 }] }] });
    api.reconcileManifestEntry.mockResolvedValue({ status: 'applied', manifest_status: 'applied' });
    render(<ManifestImportPage />);
    await screen.findByText(/Permanent container/);
    const file = new File(['123\n123\n'], 'ao1.txt', { type: 'text/plain' });
    fireEvent.change(screen.getByLabelText('Manifest file'), { target: { files: [file] } });
    fireEvent.change(screen.getByLabelText('Duplicate scan policy'), { target: { value: 'quantity' } });
    fireEvent.click(screen.getByRole('button', { name: 'Preview import' }));
    expect(await screen.findByText(/Existing inventory needs reconciliation/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm import' }));
    expect(await screen.findByText('Scanner · 123')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Source location for 123'), { target: { value: 'container-9' } });
    fireEvent.click(screen.getByRole('button', { name: 'Move stock' }));
    await waitFor(() => expect(api.reconcileManifestEntry).toHaveBeenCalledWith(expect.objectContaining({ manifestId: 22, entryId: 7, action: 'move', quantity: 2, sourceLocationKey: 'container-9' })));
  });
});
