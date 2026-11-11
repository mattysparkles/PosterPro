import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  fetchInventoryTote: vi.fn(async () => ({
    tote: { code: 'A01' },
    items: [
      { product_id: 7, title: 'Blue organizer', barcode: '036000291452', on_hand: 2, available: 2, status: 'verified', identification_status: 'identified' },
      { product_id: 8, title: 'Unidentified item (X00556MVCF)', barcode: 'X00556MVCF', on_hand: 0, available: 0, status: 'pending_reconciliation', identification_status: 'unidentified', warning: 'Location reconciliation required.' },
    ],
    summary: { product_count: 2, on_hand: 2, available: 2, unresolved_location_claims: 1 },
  })),
}));
const router = vi.hoisted(() => ({ isReady: true, query: { toteSlug: 'tote-a01' } }));

vi.mock('next/router', () => ({ useRouter: () => router }));
vi.mock('next/head', () => ({ default: ({ children }) => <>{children}</> }));
vi.mock('next/link', () => ({ default: ({ children, ...props }) => <a {...props}>{children}</a> }));
vi.mock('../components/layout/AppShell', () => ({ default: ({ children }) => <div>{children}</div> }));
vi.mock('../lib/api', () => api);

import PermanentTotePage from '../pages/inventory/[toteSlug]';

describe('permanent NFC tote pages', () => {
  beforeEach(() => {
    api.fetchInventoryTote.mockClear();
    router.query = { toteSlug: 'tote-a01' };
  });

  it('reads the live manifest without provisioning tote identities and links the active tote to the scanner', async () => {
    render(<PermanentTotePage />);
    expect(await screen.findByRole('heading', { name: 'Tote A01' })).toBeTruthy();
    expect(await screen.findByText('Blue organizer')).toBeTruthy();
    expect(await screen.findByText(/Raw barcode: X00556MVCF/)).toBeTruthy();
    expect(screen.getByText(/not included in confirmed stock totals/)).toBeTruthy();
    expect(screen.getAllByRole('link', { name: 'Resolve item / look up barcode' }).some((link) => link.getAttribute('href') === '/packing?tote=A01&resolve=X00556MVCF')).toBe(true);
    expect(screen.getByText(/inventory\/tote-a01/)).toBeTruthy();
    await waitFor(() => expect(api.fetchInventoryTote).toHaveBeenCalledWith('A01'));
    expect(screen.getByRole('link', { name: 'Scan into Tote A01' }).getAttribute('href')).toBe('/packing?tote=A01');
  });

  it('does not initialize records for an unknown slug', async () => {
    router.query = { toteSlug: 'tote-z99' };
    render(<PermanentTotePage />);
    expect(screen.getByText(/does not match one of PosterPro totes/)).toBeTruthy();
  });
});
